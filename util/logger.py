"""Logging utilities for agent observations and tool results."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.config import Config
from util.git_utils import get_repo_name


def _get_log_directory(workspace_root: Path, config: Config, metadata: dict) -> Path:
    """Get the log directory path for the current run.
    
    Args:
        workspace_root: Root directory of the workspace.
        config: Configuration object.
        metadata: Metadata dictionary from results.
    
    Returns:
        Path to the log directory.
    """
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
    
    return log_dir


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
    expert_analyses = metadata.get("expert_analyses", [])
    
    # If no observations and no expert analyses, return None
    if not observations and not expert_analyses:
        return None
    
    # Get log directory
    log_dir = _get_log_directory(workspace_root, config, metadata)
    repo_name = get_repo_name(workspace_root).replace("/", "_").replace("\\", "_").replace("..", "")
    model_name = metadata.get("config_provider", config.llm.provider) or "unknown"
    model_name = model_name.replace("/", "_").replace("\\", "_")
    
    # Save observations to log file (if any)
    log_file = log_dir / "observations.log"
    has_content = False
    
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"Agent Observations Log\n")
        f.write(f"{'=' * 80}\n")
        f.write(f"Repository: {repo_name}\n")
        f.write(f"Model: {model_name}\n")
        f.write(f"Timestamp: {datetime.now().isoformat()}\n")
        f.write(f"{'=' * 80}\n\n")
        
        # Save agent observations (old format)
        if observations:
            has_content = True
            f.write(f"Agent Observations: {len(observations)}\n")
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
    
    # Save expert analyses to separate file
    if expert_analyses:
        expert_log_file = log_dir / "expert_analyses.log"
        with open(expert_log_file, "w", encoding="utf-8") as f:
            f.write(f"Expert Analysis Log\n")
            f.write(f"{'=' * 80}\n")
            f.write(f"Repository: {repo_name}\n")
            f.write(f"Model: {model_name}\n")
            f.write(f"Timestamp: {datetime.now().isoformat()}\n")
            f.write(f"Total Expert Analyses: {len(expert_analyses)}\n")
            f.write(f"{'=' * 80}\n\n")
            
            for i, analysis in enumerate(expert_analyses, 1):
                f.write(f"Expert Analysis {i}:\n")
                f.write(f"{'=' * 80}\n")
                f.write(f"Risk Type: {analysis.get('risk_type', 'unknown')}\n")
                f.write(f"File: {analysis.get('file_path', 'unknown')}\n")
                f.write(f"Line: {analysis.get('line_number', 0)}\n")
                f.write(f"{'-' * 80}\n\n")
                
                # Original risk item
                risk_item = analysis.get("risk_item", {})
                f.write(f"Original Risk Item:\n")
                f.write(f"{json.dumps(risk_item, indent=2, ensure_ascii=False)}\n\n")
                
                # Multi-turn conversation (new format)
                conversation_turns = analysis.get("conversation_turns", [])
                if conversation_turns:
                    f.write(f"Conversation Turns ({len(conversation_turns)}):\n")
                    f.write(f"{'=' * 80}\n\n")
                    
                    for turn_num, turn in enumerate(conversation_turns, 1):
                        f.write(f"Turn {turn_num} (Iteration {turn.get('iteration', turn_num)}):\n")
                        f.write(f"{'-' * 80}\n\n")
                        
                        # Prompt for this turn
                        # prompt = turn.get("prompt", "")
                        # if prompt:
                        #     f.write(f"Prompt:\n")
                        #     f.write(f"{prompt}\n\n")
                        
                        # Response
                        response = turn.get("response", "")
                        if response:
                            f.write(f"LLM Response:\n")
                            f.write(f"{response}\n\n")
                        
                        # Tool calls in this turn
                        tool_calls = turn.get("tool_calls", [])
                        if tool_calls:
                            f.write(f"Tool Calls ({len(tool_calls)}):\n")
                            for j, tool_call in enumerate(tool_calls, 1):
                                f.write(f"  Tool Call {j}:\n")
                                f.write(f"    Tool: {tool_call.get('tool', 'unknown')}\n")
                                f.write(f"    Input: {json.dumps(tool_call.get('input', {}), indent=6, ensure_ascii=False)}\n")
                            f.write(f"\n")
                        
                        # Tool results in this turn
                        tool_results = turn.get("tool_results", {})
                        if tool_results:
                            f.write(f"Tool Results:\n")
                            for tool_name, tool_result in tool_results.items():
                                f.write(f"  {tool_name}:\n")
                                f.write(f"    {json.dumps(tool_result, indent=6, ensure_ascii=False)}\n")
                            f.write(f"\n")
                        
                        f.write(f"\n")
                else:
                    # Legacy format (single turn) - for backward compatibility
                    prompt = analysis.get("prompt", "")
                    if prompt:
                        f.write(f"Prompt:\n")
                        f.write(f"{'-' * 80}\n")
                        f.write(f"{prompt}\n\n")
                    
                    response = analysis.get("response", "")
                    if response:
                        f.write(f"Initial LLM Response:\n")
                        f.write(f"{'-' * 80}\n")
                        f.write(f"{response}\n\n")
                    
                    tool_calls = analysis.get("tool_calls", [])
                    if tool_calls:
                        f.write(f"Tool Calls ({len(tool_calls)}):\n")
                        f.write(f"{'-' * 80}\n")
                        for j, tool_call in enumerate(tool_calls, 1):
                            f.write(f"Tool Call {j}:\n")
                            f.write(f"  Tool: {tool_call.get('tool', 'unknown')}\n")
                            f.write(f"  Input: {json.dumps(tool_call.get('input', {}), indent=4, ensure_ascii=False)}\n")
                            f.write(f"\n")
                    
                    tool_results = analysis.get("tool_results", {})
                    if tool_results:
                        f.write(f"Tool Results:\n")
                        f.write(f"{'-' * 80}\n")
                        for tool_name, tool_result in tool_results.items():
                            f.write(f"{tool_name}:\n")
                            f.write(f"{json.dumps(tool_result, indent=4, ensure_ascii=False)}\n")
                            f.write(f"\n")
                
                # Final response
                final_response = analysis.get("final_response", "")
                if final_response:
                    f.write(f"{'=' * 80}\n")
                    f.write(f"Final LLM Response:\n")
                    f.write(f"{'-' * 80}\n")
                    f.write(f"{final_response}\n\n")
                
                # Validated item
                validated_item = analysis.get("validated_item")
                if validated_item:
                    f.write(f"Validated Risk Item:\n")
                    f.write(f"{'-' * 80}\n")
                    f.write(f"{json.dumps(validated_item, indent=2, ensure_ascii=False)}\n\n")
                
                # Error (if any)
                error = analysis.get("error")
                if error:
                    f.write(f"Error:\n")
                    f.write(f"{'-' * 80}\n")
                    f.write(f"{error}\n\n")
                
                f.write(f"\n")
        
        # Return expert log file if it exists, otherwise observations log
        return expert_log_file if expert_analyses else (log_file if has_content else None)
    
    return log_file if has_content else None
