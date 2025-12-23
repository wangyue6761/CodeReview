"""Intent Analysis Node for the code review workflow.

重构说明：
- 使用 LCEL (LangChain Expression Language) 语法：prompt | llm | parser
- 这是 LangGraph 标准做法，替代直接调用 llm_provider.generate()
- 节点接收 state 作为输入，返回 state 的更新部分（Partial Update）

This node implements a Map-Reduce pattern to analyze the intent of changed files
in parallel. Each file is analyzed independently, and results are aggregated.
"""

import asyncio
import logging
import json
import re
from typing import Dict, Any
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.messages import HumanMessage, SystemMessage
from core.state import ReviewState, FileAnalysis, RiskItem, RiskType
from core.llm import LLMProvider
from core.langchain_llm import LangChainLLMAdapter
from agents.prompts import render_prompt_template
from util.diff_utils import generate_context_text_for_file

logger = logging.getLogger(__name__)


async def intent_analysis_node(state: ReviewState) -> Dict[str, Any]:
    """Analyze the intent of all changed files in parallel (Map-Reduce pattern).
    
    This function processes all changed files in parallel and aggregates results.
    
    Args:
        state: Current workflow state with changed_files.
    
    Returns:
        Dictionary with 'file_analyses' key containing a list of FileAnalysis objects.
    """
    print("\n" + "="*80)
    print("📋 [节点1] Intent Analysis - 并行分析文件意图")
    print("="*80)
    
    # Get LLM provider from metadata (injected by workflow)
    llm_provider: LLMProvider = state.get("metadata", {}).get("llm_provider")
    if not llm_provider:
        logger.error("LLM provider not found in metadata")
        return {"file_analyses": []}
    
    # Get config for concurrency control
    config = state.get("metadata", {}).get("config")
    max_concurrent = config.system.max_concurrent_llm_requests if config else 5
    
    changed_files = state.get("changed_files", [])
    if not changed_files:
        print("  ⚠️  没有需要分析的文件")
        logger.warning("No changed files to analyze")
        return {"file_analyses": []}
    
    print(f"  📁 待分析文件数: {len(changed_files)}")
    print(f"  🔒 并发控制: Semaphore(max={max_concurrent})")
    print(f"  📝 文件列表:")
    for i, file_path in enumerate(changed_files, 1):
        print(f"     {i}. {file_path}")
    
    diff_context = state.get("diff_context", "")
    
    # Create semaphore for concurrency control
    semaphore = asyncio.Semaphore(max_concurrent)
    
    # 获取 LangChain LLM 适配器（从 metadata 或创建新实例）
    llm_adapter = state.get("metadata", {}).get("llm_adapter")
    if not llm_adapter:
        # 如果没有适配器，从 llm_provider 创建
        llm_provider = state.get("metadata", {}).get("llm_provider")
        if llm_provider:
            llm_adapter = LangChainLLMAdapter(llm_provider=llm_provider)
        else:
            logger.error("LLM provider not found in metadata")
            return {"file_analyses": []}
    
    # Process all files in parallel
    async def analyze_file(file_path: str) -> FileAnalysis:
        """Analyze a single file using LCEL syntax.
        
        重构说明：
        - 使用 LCEL 语法：prompt | llm | parser
        - 替代直接调用 llm_provider.generate()
        """
        async with semaphore:
            try:
                print(f"  🔍 分析中: {file_path}")
                # Extract relevant diff section for this file with line numbers
                # TODO： 思考一下，diff要不要传入remove行
                file_diff = _extract_file_diff(diff_context, file_path)
                
                # 使用 LCEL 语法创建链：prompt | llm | parser
                # 重构说明：由于 render_prompt_template 已经渲染了模板（包含 JSON 示例），
                # 我们不能使用 ChatPromptTemplate（它会尝试解析 JSON 中的大括号作为变量）
                # 应该直接使用 HumanMessage 和 SystemMessage
                
                # 获取危险模式配置（从 metadata 或使用默认值）
                dangerous_patterns = state.get("metadata", {}).get(
                    "dangerous_patterns",
                    "（危险模式配置将在后续填充）"
                )
                
                # 渲染提示模板（已经完成变量替换）
                rendered_prompt = render_prompt_template(
                    "intent_analysis",
                    file_path=file_path,
                    file_diff=file_diff,
                    diff_context=diff_context[:2000],  # Limit context size
                    dangerous_patterns=dangerous_patterns
                )
                
                # 重构说明：使用 PydanticOutputParser 直接解析为 FileAnalysis 模型
                # 这是 LangGraph 标准做法，替代手动 JSON 解析
                parser = PydanticOutputParser(pydantic_object=FileAnalysis)
                
                # 创建消息列表（直接使用已渲染的文本，并添加格式说明）
                messages = [
                    SystemMessage(content="You are an expert code reviewer analyzing file changes."),
                    HumanMessage(content=rendered_prompt + "\n\n" + parser.get_format_instructions())
                ]
                
                # 使用 LCEL 语法：messages -> llm -> parser
                try:
                    # 调用 LLM
                    response = await llm_adapter.ainvoke(messages, temperature=0.3)
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


def _extract_file_diff(diff_context: str, file_path: str) -> str:
    """Extract the diff section for a specific file with absolute line numbers.
    
    This function uses the unidiff library to parse the Git diff and generate
    code context with absolute line numbers in the new file (HEAD version).
    This enables accurate line number references in review comments.
    
    Args:
        diff_context: Full diff context.
        file_path: Path to the file (relative to repo root).
    
    Returns:
        Formatted code context text with absolute line numbers for the new file.
        Falls back to raw diff section if parsing fails.
    """
    try:
        # Use diff_utils to generate context with line numbers
        context_text = generate_context_text_for_file(
            diff_content=diff_context,
            file_path=file_path,
            include_context_lines=True,
            max_context_lines=5
        )
        
        if context_text:
            return context_text
        else:
            # If no context found, fall back to raw diff extraction
            logger.debug(f"Could not generate context with line numbers for {file_path}, falling back to raw diff")
    except Exception as e:
        # If parsing fails, fall back to raw diff extraction
        logger.warning(f"Failed to parse diff with line numbers for {file_path}: {e}, falling back to raw diff")
    
    # Fallback: Extract raw diff section using regex (original behavior)
    patterns = [
        rf"diff --git.*{re.escape(file_path)}.*?\n(.*?)(?=\ndiff --git|\Z)",
        rf"--- a/{re.escape(file_path)}.*?\n(.*?)(?=\n--- a/|\Z)",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, diff_context, re.DOTALL)
        if match:
            return match.group(0)
    
    # If no specific section found, return a portion of the diff
    return diff_context[:1000] if diff_context else ""


# 重构说明：_parse_intent_analysis_response_from_dict 函数已被移除
# 现在使用 PydanticOutputParser 直接解析为 FileAnalysis 模型
# 这样可以：
# 1. 自动验证所有字段类型（包括 RiskItem 中的 line_number）
# 2. 自动处理嵌套的 RiskItem 列表验证
# 3. 提供更好的错误信息
# 4. 符合 LangGraph 标准做法

def _parse_intent_analysis_response(response: str, file_path: str) -> FileAnalysis:
    """Parse LLM response into FileAnalysis object.
    
    Args:
        response: LLM response string.
        file_path: Path to the analyzed file.
    
    Returns:
        FileAnalysis object.
    """
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
                    # 修复说明：line_number 是必需字段，不能为 None 或无效
                    # 必须提供 [start, end] 格式，field_validator 会验证格式
                    line_number = risk_data.get("line_number")
                    if line_number is None:
                        logger.error(f"Missing line_number in risk item: {risk_data}, file_path: {file_path}")
                        continue
                    
                    # field_validator 会验证 line_number 必须是 [start, end] 格式
                    # 如果格式不正确会抛出 ValueError
                    
                    risk_item = RiskItem(
                        risk_type=RiskType(risk_data.get("risk_type", "null_safety")),
                        file_path=risk_data.get("file_path", file_path),
                        line_number=line_number,  # 必须是 [start, end] 格式
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
