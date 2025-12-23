"""Reporter Node for the code review workflow.

This node generates the final review report by aggregating expert results
and applying confidence filtering.
"""

import logging
from typing import Dict, Any, List
from core.state import ReviewState, RiskItem
from core.llm import LLMProvider
from agents.prompts import render_prompt_template

logger = logging.getLogger(__name__)


async def reporter_node(state: ReviewState) -> Dict[str, Any]:
    """Generate final review report from expert results.
    
    This node:
    1. Collects all expert_results
    2. Filters by confidence threshold
    3. Generates final report
    4. Updates confirmed_issues
    
    Args:
        state: Current workflow state with expert_results.
    
    Returns:
        Dictionary with 'confirmed_issues' and 'final_report' keys.
    """
    print("\n" + "="*80)
    print("📊 [节点4] Reporter - 生成最终报告")
    print("="*80)
    
    # Get LLM provider from metadata
    llm_provider: LLMProvider = state.get("metadata", {}).get("llm_provider")
    if not llm_provider:
        logger.error("LLM provider not found in metadata")
        return {"confirmed_issues": [], "final_report": "Error: LLM provider not available"}
    
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
    confirmed_issues = [
        item for item in all_results
        if item.confidence >= confidence_threshold
    ]
    
    print(f"  🔍 按置信度过滤 (阈值: {confidence_threshold})")
    print(f"     - 总结果数: {len(all_results)}")
    print(f"     - 确认问题数: {len(confirmed_issues)}")
    
    logger.info(f"Reporter: {len(all_results)} total results, "
                f"{len(confirmed_issues)} confirmed issues (threshold: {confidence_threshold})")
    
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
        
        final_report = await llm_provider.generate(prompt, temperature=0.3)
        
        print(f"  ✅ Reporter 完成!")
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
    """Generate a simple text report from confirmed issues.
    
    Args:
        confirmed_issues: List of confirmed RiskItem objects.
    
    Returns:
        Simple text report.
    """
    if not confirmed_issues:
        return "No issues found. Code review completed successfully."
    
    report_lines = [
        "# Code Review Report",
        f"\nTotal Issues Found: {len(confirmed_issues)}\n",
        "## Issues by Severity\n"
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
            report_lines.append(f"\n### {severity.upper()} ({len(by_severity[severity])})")
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
                    report_lines.append(f"  💡 Suggestion: {issue.suggestion}")
    
    return "\n".join(report_lines)
