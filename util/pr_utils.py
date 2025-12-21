"""PR (Pull Request) processing utilities for diff loading and result formatting."""

from pathlib import Path
from typing import Optional

from core.config import Config
from util.git_utils import get_repo_name
from util.logger import save_observations_to_log


def load_diff_from_file(file_path: Path) -> str:
    """Load Git diff from a file.
    
    Args:
        file_path: Path to the diff file.
    
    Returns:
        The diff content as a string.
    
    Raises:
        FileNotFoundError: If the file doesn't exist.
        IOError: If the file cannot be read.
    """
    file_path = Path(file_path).resolve()
    
    if not file_path.exists():
        raise FileNotFoundError(f"Diff file not found: {file_path}")
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        raise IOError(f"Error reading diff file: {e}")


def print_review_results(results: dict, workspace_root: Optional[Path] = None, config: Optional[Config] = None) -> None:
    """Print the review results in a formatted way.
    
    Args:
        results: The final state dictionary from the workflow.
        workspace_root: Optional workspace root path for log saving.
        config: Optional configuration object for log saving.
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
                    line = issue.get("line_number") or issue.get("line", 0)
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
    
    # Metadata
    metadata = results.get("metadata", {})
    if metadata:
        print(f"\n📊 Metadata:")
        for key, value in metadata.items():
            # Skip printing observations in metadata (will be in log file)
            if key == "agent_observations":
                print(f"  • {key}: [{len(value) if isinstance(value, list) else 0} observations] (saved to log)")
            elif key == "agent_tool_results":
                print(f"  • {key}: [{len(value) if isinstance(value, list) else 0} tool calls] (saved to log)")
            elif key == "expert_analyses":
                print(f"  • {key}: [{len(value) if isinstance(value, list) else 0} expert analyses] (saved to log)")
            elif key in ["llm_provider", "config", "tools"]:
                # Skip non-serializable objects
                print(f"  • {key}: [object] (not serialized)")
            else:
                print(f"  • {key}: {value}")
    
    # Save observations and expert analyses to log files
    if workspace_root and config:
        try:
            log_file = save_observations_to_log(results, workspace_root, config)
            if log_file:
                print(f"\n📝 Logs saved:")
                print(f"   • Observations: {log_file}")
                
                # Check if expert analyses log exists (same directory)
                expert_log_file = log_file.parent / "expert_analyses.log"
                if expert_log_file.exists():
                    print(f"   • Expert Analyses: {expert_log_file}")
        except Exception as e:
            print(f"\n⚠️  Warning: Could not save logs: {e}")
    
    print("\n" + "=" * 80)
