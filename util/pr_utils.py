"""PR（拉取请求）处理工具，用于 diff 加载和结果格式化。"""

import json
import re
from pathlib import Path
from typing import Optional

from core.config import Config
from util.git_utils import get_repo_name
from util.logger import save_observations_to_log


def load_diff_from_file(file_path: Path) -> str:
    """从文件加载 Git diff。
    
    Raises:
        FileNotFoundError: 文件不存在。
        IOError: 文件无法读取。
    """
    file_path = Path(file_path).resolve()
    
    if not file_path.exists():
        raise FileNotFoundError(f"Diff file not found: {file_path}")
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        raise IOError(f"Error reading diff file: {e}")


def print_review_results(
    results: dict, 
    workspace_root: Optional[Path] = None, 
    config: Optional[Config] = None,
    base_branch: Optional[str] = None,
    head_branch: Optional[str] = None,
    timestamp: Optional[str] = None
) -> None:
    """以格式化方式打印审查结果。
    
    Args:
        results: 审查结果字典。
        workspace_root: 工作区根目录（可选）。
        config: 配置对象（可选）。
        base_branch: base分支名（可选）。
        head_branch: head分支名（可选）。
        timestamp: 时间戳字符串（可选），用于确保log和results使用相同的时间戳。
    """
    print("\n" + "=" * 80)
    print("CODE REVIEW RESULTS")
    print("=" * 80)
    
    # Changed files (for multi-agent workflow) or focus files (for old workflow)
    changed_files = results.get("changed_files", [])
    focus_files = results.get("focus_files", [])
    files_to_show = changed_files if changed_files else focus_files
    
    print(f"\n📋 Changed Files ({len(files_to_show)}):")
    if files_to_show:
        for i, file_path in enumerate(files_to_show, 1):
            print(f"  {i}. {file_path}")
    else:
        print("  (none)")
    
    # Issues - support both old format (identified_issues) and new format (confirmed_issues)
    identified_issues = results.get("identified_issues", [])
    confirmed_issues = results.get("confirmed_issues", [])
    issues = confirmed_issues if confirmed_issues else identified_issues
    
    print(f"\n🔍 Issues Found ({len(issues)}):")
    
    if not issues:
        print("  ✅ No issues found!")
    else:
        # Group by severity
        by_severity = {"error": [], "warning": [], "info": []}
        for issue in issues:
            # Support both old format (severity) and new format (RiskItem with severity)
            severity = issue.get("severity", "info")
            by_severity.get(severity, by_severity["info"]).append(issue)
        
        for severity in ["error", "warning", "info"]:
            severity_issues = by_severity[severity]
            if severity_issues:
                icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}[severity]
                print(f"\n  {icon} {severity.upper()} ({len(severity_issues)}):")
                for issue in severity_issues:
                    # Support both old format and new RiskItem format
                    file_path = issue.get("file_path") or issue.get("file", "unknown")
                    line_number = issue.get("line_number") or issue.get("line", 0)
                    # Format line number range: (10, 15) -> "10:15", (10, 10) or 10 -> "10"
                    if isinstance(line_number, (list, tuple)) and len(line_number) == 2:
                        start, end = line_number
                        line = f"{start}:{end}" if start != end else str(start)
                    else:
                        line = str(line_number) if line_number else "0"
                    message = issue.get("description") or issue.get("message", "")
                    suggestion = issue.get("suggestion", "")
                    risk_type = issue.get("risk_type", "")
                    confidence = issue.get("confidence")
                    
                    # Format risk type if available
                    risk_type_str = f" [{risk_type}]" if risk_type else ""
                    confidence_str = f" (confidence: {confidence:.2f})" if confidence is not None else ""
                    
                    print(f"    • {file_path}:{line}{risk_type_str}{confidence_str}")
                    print(f"      {message}")
                    if suggestion:
                        print(f"      💡 Suggestion: {suggestion}")
    
    # Final report (for multi-agent workflow)
    final_report = results.get("final_report", "")
    if final_report:
        print(f"\n📄 Final Report:")
        print("  " + "=" * 76)
        # Print first 500 characters of the report
        report_preview = final_report[:500] + "..." if len(final_report) > 500 else final_report
        for line in report_preview.split("\n"):
            print(f"  {line}")
        if len(final_report) > 500:
            print(f"  ... (truncated, {len(final_report)} total characters)")
        print("  " + "=" * 76)
    
    # Metadata (skip langchain_tools and other verbose fields)
    metadata = results.get("metadata", {})
    if metadata:
        def _redact_text(s: str) -> str:
            # Best-effort redaction for accidental secret leakage in object repr / logs.
            if not isinstance(s, str) or not s:
                return s
            patterns = [
                r"(api_key=)('[^']*'|\"[^\"]*\"|[^,\s]+)",
                r"(token=)('[^']*'|\"[^\"]*\"|[^,\s]+)",
                r"(secret=)('[^']*'|\"[^\"]*\"|[^,\s]+)",
            ]
            out = s
            for pat in patterns:
                out = re.sub(pat, r"\1***REDACTED***", out, flags=re.IGNORECASE)
            return out

        def _safe_format(v: object) -> str:
            try:
                if isinstance(v, str):
                    return _redact_text(v)
                return _redact_text(str(v))
            except Exception:
                return "<unprintable>"

        print(f"\n📊 Metadata:")
        for key, value in metadata.items():
            # Skip printing observations in metadata (will be in log file)
            if key == "agent_observations":
                print(f"  • {key}: [{len(value) if isinstance(value, list) else 0} observations] (saved to log)")
            elif key == "agent_tool_results":
                print(f"  • {key}: [{len(value) if isinstance(value, list) else 0} tool calls] (saved to log)")
            elif key == "expert_analyses":
                print(f"  • {key}: [{len(value) if isinstance(value, list) else 0} expert analyses] (saved to log)")
            elif key in ["llm_provider", "config", "tools", "langchain_tools", "llm"]:
                # Skip non-serializable objects and langchain_tools
                continue
            else:
                print(f"  • {key}: {_safe_format(value)}")
    
    # Save observations and expert analyses to log files
    if workspace_root and config:
        try:
            log_file = save_observations_to_log(
                results, 
                workspace_root, 
                config,
                base_branch=base_branch,
                head_branch=head_branch,
                timestamp=timestamp
            )
            if log_file:
                print(f"\n📝 Logs saved:")
                print(f"   • Expert Analyses: {log_file}")
        except Exception as e:
            print(f"\n⚠️  Warning: Could not save logs: {e}")
    
    print("\n" + "=" * 80)


def make_results_serializable(obj: dict) -> dict:
    """移除字典中的不可序列化对象（如 ChatModel、Config、tools）。
    
    简化结果结构，只保留最终报告和基本信息：
    - 保留 changed_files 字段（基本信息）
    - 保留 final_report 字段（最终报告）
    - 移除所有其他详细数据（diff_context, metadata, work_list, expert_tasks, expert_results, confirmed_issues, risk_analyses 等）
    
    Args:
        obj: 可能包含不可序列化对象的字典。
    
    Returns:
        仅包含可序列化值的简化字典。
    """
    if not isinstance(obj, dict):
        return obj
    
    result = {}
    
    # Only keep basic information: changed_files
    if "changed_files" in obj:
        changed_files = obj["changed_files"]
        if isinstance(changed_files, list):
            result["changed_files"] = changed_files
        else:
            result["changed_files"] = []
    
    # Keep final_report (the main output)
    final_report = obj.get("final_report", "")
    if final_report:
        result["final_report"] = final_report
    
    return result


def serialize_messages(messages: list) -> list:
    """序列化 LangChain 消息列表。
    
    不包含 tool_calls 字段，因为工具调用信息已经在 ToolMessage 的 content 中。
    
    Args:
        messages: LangChain 消息列表。
    
    Returns:
        可序列化的消息字典列表。
    """
    serialized = []
    for msg in messages:
        msg_dict = {
            "type": type(msg).__name__,
            "content": getattr(msg, 'content', str(msg))
        }
        
        # 不包含 tool_calls 字段，因为工具调用信息已经在 ToolMessage 的 content 中
        
        if hasattr(msg, 'name'):
            msg_dict["name"] = msg.name
        
        if hasattr(msg, 'tool_call_id'):
            msg_dict["tool_call_id"] = msg.tool_call_id
        
        serialized.append(msg_dict)
    
    return serialized
