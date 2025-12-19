"""Logging utilities for agent observations and tool results."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.config import Config
from util.git_utils import get_repo_name


def save_observations_to_log(
    results: dict,
    workspace_root: Path,
    config: Config
) -> Optional[Path]:
    """Save agent observations to a log file.
    
    Log file structure: log/repo_name/model_name/timestamp/observations.log
    
    Args:
        results: The final state dictionary from the workflow.
        workspace_root: Root directory of the workspace.
        config: Configuration object.
    
    Returns:
        Path to the saved log file, or None if no observations to save.
    """
    metadata = results.get("metadata", {})
    observations = metadata.get("agent_observations", [])
    
    if not observations:
        return None
    
    # Get repo name
    repo_name = get_repo_name(workspace_root)
    # Sanitize repo name for filesystem
    repo_name = repo_name.replace("/", "_").replace("\\", "_").replace("..", "")
    
    # Get model name from metadata or config
    model_name = metadata.get("config_provider", config.llm.provider)
    if not model_name:
        model_name = "unknown"
    # Sanitize model name
    model_name = model_name.replace("/", "_").replace("\\", "_")
    
    # Generate timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Create log directory structure
    log_dir = Path("log") / repo_name / model_name / timestamp
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Save observations to log file
    log_file = log_dir / "observations.log"
    
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"Agent Observations Log\n")
        f.write(f"{'=' * 80}\n")
        f.write(f"Repository: {repo_name}\n")
        f.write(f"Model: {model_name}\n")
        f.write(f"Timestamp: {datetime.now().isoformat()}\n")
        f.write(f"Total Observations: {len(observations)}\n")
        f.write(f"{'=' * 80}\n\n")
        
        for i, obs in enumerate(observations, 1):
            f.write(f"Observation {i}:\n")
            f.write(f"{'-' * 80}\n")
            f.write(f"{obs}\n")
            f.write(f"\n")
        
        # Also save tool results if available
        tool_results = metadata.get("agent_tool_results", [])
        if tool_results:
            f.write(f"\n{'=' * 80}\n")
            f.write(f"Tool Results: {len(tool_results)}\n")
            f.write(f"{'=' * 80}\n\n")
            for i, tr in enumerate(tool_results, 1):
                f.write(f"Tool Call {i}:\n")
                f.write(f"{'-' * 80}\n")
                f.write(f"Tool: {tr.get('tool', 'unknown')}\n")
                f.write(f"Input: {json.dumps(tr.get('input', {}), indent=2, ensure_ascii=False)}\n")
                f.write(f"Result: {json.dumps(tr.get('result', {}), indent=2, ensure_ascii=False)}\n")
                f.write(f"\n")
    
    return log_file
