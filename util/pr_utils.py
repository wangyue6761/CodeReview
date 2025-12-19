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
    
    # Focus files
    focus_files = results.get("focus_files", [])
    print(f"\n📋 Focus Files ({len(focus_files)}):")
    for i, file_path in enumerate(focus_files, 1):
        print(f"  {i}. {file_path}")
    
    # Issues
    issues = results.get("identified_issues", [])
    print(f"\n🔍 Issues Found ({len(issues)}):")
    
    if not issues:
        print("  ✅ No issues found!")
    else:
        # Group by severity
        by_severity = {"error": [], "warning": [], "info": []}
        for issue in issues:
            severity = issue.get("severity", "info")
            by_severity.get(severity, by_severity["info"]).append(issue)
        
        for severity in ["error", "warning", "info"]:
            severity_issues = by_severity[severity]
            if severity_issues:
                icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}[severity]
                print(f"\n  {icon} {severity.upper()} ({len(severity_issues)}):")
                for issue in severity_issues:
                    file_path = issue.get("file", "unknown")
                    line = issue.get("line", 0)
                    message = issue.get("message", "")
                    suggestion = issue.get("suggestion", "")
                    
                    print(f"    • {file_path}:{line}")
                    print(f"      {message}")
                    if suggestion:
                        print(f"      💡 Suggestion: {suggestion}")
    
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
            else:
                print(f"  • {key}: {value}")
    
    # Save observations to log file
    if workspace_root and config:
        try:
            log_file = save_observations_to_log(results, workspace_root, config)
            if log_file:
                print(f"\n📝 Observations saved to: {log_file}")
        except Exception as e:
            print(f"\n⚠️  Warning: Could not save observations to log: {e}")
    
    print("\n" + "=" * 80)
