"""代码审查工作流的意图分析节点。

实现 Map-Reduce 模式，并行分析变更文件的意图。
使用 LCEL 语法：prompt | llm | parser。
"""

import asyncio
import logging
import json
import re
from typing import Dict, Any
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel
from core.state import ReviewState, FileAnalysis, RiskItem, RiskType
from agents.prompts import render_prompt_template
from util.diff_utils import generate_context_text_for_file, extract_file_diff
from util.file_utils import read_file_content

logger = logging.getLogger(__name__)


async def intent_analysis_node(state: ReviewState) -> Dict[str, Any]:
    """并行分析所有变更文件的意图（Map-Reduce 模式）。
    
    Returns:
        包含 'file_analyses' 键的字典。
    """
    print("\n" + "="*80)
    print("📋 [节点1] Intent Analysis - 并行分析文件意图")
    print("="*80)
    
    # Get LLM from metadata (injected by workflow)
    llm: BaseChatModel = state.get("metadata", {}).get("llm")
    if not llm:
        logger.error("LLM not found in metadata")
        return {"file_analyses": []}
    
    # Get config for concurrency control
    config = state.get("metadata", {}).get("config")
    max_concurrent = config.system.max_concurrent_llm_requests if config else 5
    
    changed_files = state.get("changed_files", [])
    if not changed_files:
        print("  ⚠️  没有需要分析的文件")
        logger.warning("No changed files to analyze")
        return {"file_analyses": []}
    
    # ===== 临时调试：文件过滤 =====
    # TODO: 调试完成后删除此代码块
    # TARGET_FILE = "src/sentry/api/endpoints/organization_auditlogs.py"  # 修改为要调试的文件路径
    # changed_files = [f for f in changed_files if f == TARGET_FILE or f.endswith(TARGET_FILE)]
    # if changed_files:
    #     print(f"  🔍 [调试模式] 过滤后只分析文件: {changed_files}")
    # else:
    #     print(f"  ⚠️  [调试模式] 目标文件 '{TARGET_FILE}' 不在变更列表中")
    #     return {"file_analyses": []}
    # ===== 临时调试代码结束 =====
    
    print(f"  📁 待分析文件数: {len(changed_files)}")
    print(f"  🔒 并发控制: Semaphore(max={max_concurrent})")
    print(f"  📝 文件列表:")
    for i, file_path in enumerate(changed_files, 1):
        print(f"     {i}. {file_path}")
    
    diff_context = state.get("diff_context", "")
    
    # Create semaphore for concurrency control
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def analyze_file(file_path: str) -> FileAnalysis:
        """使用 LCEL 语法分析单个文件。"""
        async with semaphore:
            try:
                print(f"  🔍 分析中: {file_path}")
                file_diff = extract_file_diff(diff_context, file_path)
                
                # 读取文件内容
                file_content = read_file_content(file_path, config)
                
                # 渲染提示模板
                rendered_prompt = render_prompt_template(
                    "intent_analysis",
                    file_path=file_path,
                    file_diff=file_diff,
                    file_content=file_content
                )
                
                parser = PydanticOutputParser(pydantic_object=FileAnalysis)
                
                # 创建消息列表（直接使用已渲染的文本，并添加格式说明）
                messages = [
                    SystemMessage(content="You are an expert code reviewer analyzing file changes."),
                    HumanMessage(content=rendered_prompt + "\n\n" + parser.get_format_instructions())
                ]
                
                # 使用 LCEL 语法：messages -> llm -> parser
                try:
                    # 调用 LLM
                    response = await llm.ainvoke(messages, temperature=0.3)
                    # 解析为 Pydantic 模型
                    response_text = response.content if hasattr(response, 'content') else str(response)
                    file_analysis: FileAnalysis = parser.parse(response_text)
                except Exception as e:
                    # 如果解析失败，回退到文本解析
                    logger.warning(f"PydanticOutputParser failed for {file_path}, falling back to text parsing: {e}")
                    response_text = response.content if hasattr(response, 'content') else str(response)
                    file_analysis = _parse_intent_analysis_response(response_text, file_path)
                
                print(f"  ✅ 完成: {file_path}")
                print(f"     意图摘要: {file_analysis.intent_summary[:80]}...")
                print(f"     潜在风险数: {len(file_analysis.potential_risks)}")
                logger.info(f"Analyzed intent for {file_path}: {file_analysis.intent_summary[:100]}...")
                return file_analysis
            except Exception as e:
                logger.error(f"Error analyzing intent for {file_path}: {e}")
                # Return error analysis
                return FileAnalysis(
                    file_path=file_path,
                    intent_summary=f"Error analyzing file: {str(e)}",
                    potential_risks=[],
                    complexity_score=None
                )
    
    # Process all files concurrently
    print(f"\n  🚀 开始并行分析 {len(changed_files)} 个文件...")
    file_analyses = await asyncio.gather(*[analyze_file(f) for f in changed_files])
    
    # Convert Pydantic models to dicts for state (LangGraph TypedDict compatibility)
    file_analyses_dicts = [fa.model_dump() for fa in file_analyses]
    
    total_risks = sum(len(fa.potential_risks) for fa in file_analyses)
    print(f"\n  ✅ Intent Analysis 完成!")
    print(f"     - 分析文件数: {len(file_analyses)}")
    print(f"     - 发现潜在风险: {total_risks} 个")
    print("="*80)
    logger.info(f"Completed intent analysis for {len(file_analyses)} files")

    
    return {
        "file_analyses": file_analyses_dicts
    }


def _parse_intent_analysis_response(response: str, file_path: str) -> FileAnalysis:
    """解析 LLM 响应为 FileAnalysis 对象（PydanticOutputParser 失败时的回退方案）。"""
    try:
        # Try to parse as JSON first
        response_clean = response.strip()
        if response_clean.startswith("```json"):
            response_clean = response_clean[7:]
        if response_clean.startswith("```"):
            response_clean = response_clean[3:]
        if response_clean.endswith("```"):
            response_clean = response_clean[:-3]
        response_clean = response_clean.strip()
        
        try:
            data = json.loads(response_clean)
            intent_summary = data.get("intent_summary", response[:500])
            potential_risks_data = data.get("potential_risks", [])
            complexity_score = data.get("complexity_score")
            
            # Convert potential_risks to RiskItem objects
            potential_risks = []
            for risk_data in potential_risks_data:
                try:
                    line_number = risk_data.get("line_number")
                    if line_number is None:
                        logger.error(f"Missing line_number in risk item: {risk_data}, file_path: {file_path}")
                        continue
                    
                    risk_item = RiskItem(
                        risk_type=RiskType(risk_data.get("risk_type", "null_safety")),
                        file_path=risk_data.get("file_path", file_path),
                        line_number=line_number,
                        description=risk_data.get("description", ""),
                        confidence=risk_data.get("confidence", 0.5),
                        severity=risk_data.get("severity", "info"),
                        suggestion=risk_data.get("suggestion")
                    )
                    potential_risks.append(risk_item)
                except Exception as e:
                    logger.error(f"Failed to parse risk item: {e}, risk_data: {risk_data}, file_path: {file_path}")
                    continue
            
            return FileAnalysis(
                file_path=file_path,
                intent_summary=intent_summary,
                potential_risks=potential_risks,
                complexity_score=complexity_score
            )
        except json.JSONDecodeError:
            # If JSON parsing fails, create a simple FileAnalysis from text
            return FileAnalysis(
                file_path=file_path,
                intent_summary=response[:500],
                potential_risks=[],
                complexity_score=None
            )
    except Exception as e:
        logger.error(f"Error parsing intent analysis response: {e}")
        return FileAnalysis(
            file_path=file_path,
            intent_summary=f"Error parsing response: {str(e)}",
            potential_risks=[],
            complexity_score=None
        )
