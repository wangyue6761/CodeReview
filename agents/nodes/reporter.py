"""代码审查工作流的报告生成节点。

聚合专家结果并应用置信度过滤，生成最终审查报告。
"""

import logging
from typing import Dict, Any, List
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel
from core.state import ReviewState, RiskItem
from agents.prompts import render_prompt_template
from util.runtime_utils import elapsed_tag

logger = logging.getLogger(__name__)


async def reporter_node(state: ReviewState) -> Dict[str, Any]:
    """从专家结果生成最终审查报告。
    
    Returns:
        包含 'confirmed_issues' 和 'final_report' 键的字典。
    """
    print("\n" + "="*80)
    meta = state.get("metadata") or {}
    print(f"📊 [节点4] Reporter - 生成最终报告 ({elapsed_tag(meta)})")
    print("="*80)
    
    # Get LLM from metadata
    llm: BaseChatModel = state.get("metadata", {}).get("llm")
    if not llm:
        logger.error("LLM not found in metadata")
        return {"confirmed_issues": [], "final_report": "错误：LLM 不可用"}
    
    expert_results_dicts = state.get("expert_results", {})
    diff_context = state.get("diff_context", "")
    
    # Convert dicts to Pydantic models for processing
    from core.state import RiskItem
    expert_results = {
        risk_type: [RiskItem(**item) if isinstance(item, dict) else item for item in items]
        for risk_type, items in expert_results_dicts.items()
    }
    
    # Collect all results from expert groups
    all_results: List[RiskItem] = []
    for risk_type_str, results in expert_results.items():
        all_results.extend(results)
    
    print(f"  📥 收集专家结果: {len(all_results)} 个")
    
    # Filter by confidence threshold (default: 0.5)
    confidence_threshold = state.get("metadata", {}).get("confidence_threshold", 0.5)
    config = state.get("metadata", {}).get("config")
    threshold_by_type = {}
    try:
        threshold_by_type = dict(getattr(getattr(config, "system", None), "confidence_threshold_by_risk_type", {}) or {})
    except Exception:
        threshold_by_type = {}

    confirmed_issues = [
        item for item in all_results
        if item.confidence >= float(threshold_by_type.get(item.risk_type.value, confidence_threshold))
    ]
    
    print(f"  🔍 按置信度过滤 (阈值: {confidence_threshold})")
    print(f"     - 总结果数: {len(all_results)}")
    print(f"     - 确认问题数: {len(confirmed_issues)}")
    
    logger.info(f"Reporter: {len(all_results)} total results, "
                f"{len(confirmed_issues)} confirmed issues (threshold: {confidence_threshold})")

    if not confirmed_issues:
        final_report = _generate_simple_report([])
        confirmed_issues_dicts: List[Dict[str, Any]] = []
        return {
            "confirmed_issues": confirmed_issues_dicts,
            "final_report": final_report,
        }

    try:
        # Generate final report
        print("  🤖 调用 LLM 生成最终报告...")
        prompt = render_prompt_template(
            "reporter",
            diff_context=diff_context[:3000],  # Limit context size
            confirmed_issues=[item.model_dump() for item in confirmed_issues],
            num_issues=len(confirmed_issues),
            num_files=len(state.get("changed_files", []))
        )
        
        # Use standard ChatModel interface
        messages = [
            SystemMessage(content="你是代码审查专家，请用中文输出最终审查报告。"),
            HumanMessage(content=prompt)
        ]
        response = await llm.ainvoke(messages)
        final_report = response.content if hasattr(response, 'content') else str(response)
        
        print(f"  ✅ Reporter 完成! ({elapsed_tag(meta)})")
        print(f"     - 报告长度: {len(final_report)} 字符")
        print(f"     - 确认问题: {len(confirmed_issues)} 个")
        print("="*80)
        logger.info(f"Generated final report: {len(final_report)} characters")
        
        # Convert Pydantic models to dicts for state (LangGraph TypedDict compatibility)
        confirmed_issues_dicts = [item.model_dump() for item in confirmed_issues]
        
        return {
            "confirmed_issues": confirmed_issues_dicts,
            "final_report": final_report
        }
    except Exception as e:
        logger.error(f"Error generating report: {e}")
        # Generate simple report from confirmed issues
        simple_report = _generate_simple_report(confirmed_issues)
        return {
            "confirmed_issues": confirmed_issues,
            "final_report": simple_report
        }


def _generate_simple_report(confirmed_issues: List[RiskItem]) -> str:
    """从确认的问题生成简单文本报告。"""
    if not confirmed_issues:
        return "未发现问题，代码审查已完成。"
    
    report_lines = [
        "# 代码审查报告",
        f"\n问题总数: {len(confirmed_issues)}\n",
        "## 按严重级别分类\n"
    ]
    
    # Group by severity
    by_severity = {}
    for issue in confirmed_issues:
        severity = issue.severity
        if severity not in by_severity:
            by_severity[severity] = []
        by_severity[severity].append(issue)
    
    for severity in ["error", "warning", "info"]:
        if severity in by_severity:
            report_lines.append(f"\n### {severity.upper()}（{len(by_severity[severity])}）")
            for issue in by_severity[severity]:
                # Format line number range: (10, 15) -> "10:15", (10, 10) -> "10"
                start_line, end_line = issue.line_number
                if start_line == end_line:
                    line_str = str(start_line)
                else:
                    line_str = f"{start_line}:{end_line}"
                
                report_lines.append(
                    f"- **{issue.file_path}:{line_str}** "
                    f"[{issue.risk_type.value}] "
                    f"(confidence: {issue.confidence:.2f})\n"
                    f"  {issue.description}"
                )
                if issue.suggestion:
                    report_lines.append(f"  💡 建议: {issue.suggestion}")
    
    return "\n".join(report_lines)
