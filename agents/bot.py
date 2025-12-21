"""Autonomous ReAct agent for code review.

This module implements a single autonomous agent that uses ReAct (Reasoning + Acting)
pattern to review code. The agent can use tools to fetch repository maps and read files,
making autonomous decisions about what to review.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional
from langgraph.graph import StateGraph, END
from core.state import ReviewState
from core.llm import LLMProvider
from core.config import Config
from tools.repo_tools import FetchRepoMapTool
from tools.file_tools import ReadFileTool
from agents.prompts import render_prompt_template

# Set up logger
logger = logging.getLogger(__name__)


class ReActAgent:
    
    def __init__(self, llm_provider: LLMProvider, tools: List[Any]):
        """Initialize the ReAct agent.
        
        Args:
            llm_provider: LLM provider for generating responses.
            tools: List of tools the agent can use.
        """
        self.llm_provider = llm_provider
        self.tools = {tool.name: tool for tool in tools}
        self.max_iterations = 10  # Prevent infinite loops
    
    def _extract_tool_call(self, text: str) -> Optional[Dict[str, Any]]:
        """Extract tool call from LLM response.
        
        Looks for patterns like:
        - Action: tool_name
        - Action Input: {"param": "value"}
        
        Args:
            text: LLM response text.
        
        Returns:
            Dictionary with tool_name and parameters, or None if no tool call found.
        """
        # Pattern 1: Action: tool_name\nAction Input: {...}
        pattern1 = r'Action:\s*(\w+)\s*\n\s*Action Input:\s*({.*?})'
        match1 = re.search(pattern1, text, re.DOTALL)
        if match1:
            tool_name = match1.group(1)
            try:
                action_input = json.loads(match1.group(2))
                return {"tool": tool_name, "input": action_input}
            except json.JSONDecodeError:
                pass
        
        # Pattern 2: {"tool": "tool_name", "input": {...}}
        pattern2 = r'\{"tool":\s*"(\w+)",\s*"input":\s*({.*?})\}'
        match2 = re.search(pattern2, text, re.DOTALL)
        if match2:
            tool_name = match2.group(1)
            try:
                action_input = json.loads(match2.group(2))
                return {"tool": tool_name, "input": action_input}
            except json.JSONDecodeError:
                pass
        
        return None
    
    async def _execute_tool(self, tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool with given input.
        
        Args:
            tool_name: Name of the tool to execute.
            tool_input: Input parameters for the tool.
        
        Returns:
            Tool execution result.
        """
        if tool_name not in self.tools:
            return {
                "error": f"Tool '{tool_name}' not found. Available tools: {list(self.tools.keys())}"
            }
        
        tool = self.tools[tool_name]
        try:
            result = await tool.run(**tool_input)
            return result
        except Exception as e:
            return {
                "error": f"Error executing tool {tool_name}: {str(e)}"
            }
    
    async def _agent_step(self, state: ReviewState) -> Dict[str, Any]:
        """Single step in the ReAct loop.
        
        Args:
            state: Current agent state.
        
        Returns:
            Updated state dictionary.
        """
        # Get current iteration count
        iterations = state.get("metadata", {}).get("agent_iterations", 0)
        if iterations >= self.max_iterations:
            return {
                "metadata": {
                    **state.get("metadata", {}),
                    "agent_status": "max_iterations_reached",
                    "agent_final_note": "Reached maximum iterations. Generating final review."
                }
            }
        
        # Build context from state
        pr_diff = state.get("pr_diff", "")
        observations = state.get("metadata", {}).get("agent_observations", [])
        tool_results = state.get("metadata", {}).get("agent_tool_results", [])
        tool_failures = state.get("metadata", {}).get("agent_tool_failures", 0)
        
        # Count recent consecutive failures
        recent_failures = 0
        for tr in reversed(tool_results[-5:]):
            result = tr.get("result", {})
            if isinstance(result, dict) and result.get("error"):
                recent_failures += 1
            else:
                break
        
        # Build full context - no truncation
        # Format all observations
        observations_text = "\n".join([
            f"Observation {i+1}: {obs}" for i, obs in enumerate(observations)
        ]) if observations else "No observations yet."
        
        # Format all tool results - full content, no truncation
        tool_results_text = "\n".join([
            f"Tool Call {i+1}: {tr.get('tool', 'unknown')}\n"
            f"  Input: {json.dumps(tr.get('input', {}), indent=2, ensure_ascii=False)}\n"
            f"  Result: {json.dumps(tr.get('result', {}), indent=2, ensure_ascii=False)}"
            for i, tr in enumerate(tool_results)
        ]) if tool_results else "No tool calls yet."
        
        available_tools = ", ".join(self.tools.keys())
        remaining_iterations = self.max_iterations - iterations
        
        # Build dynamic guidance
        tool_guidance = ""
        if recent_failures >= 3:
            tool_guidance = "\n⚠️ WARNING: Multiple recent tool calls have failed. Consider proceeding without additional tool calls."
        elif recent_failures >= 2:
            tool_guidance = "\n⚠️ Note: Some recent tool calls failed. Be cautious about retrying the same tool."
        
        max_iterations_warning = ""
        if remaining_iterations <= 2:
            max_iterations_warning = f"\n⚠️ IMPORTANT: You are approaching the maximum iteration limit ({remaining_iterations} remaining). Please provide your final review now."
        
        # Load prompt template from file
        prompt = render_prompt_template(
            "react_agent",
            pr_diff=pr_diff,
            current_iteration=iterations + 1,
            max_iterations=self.max_iterations,
            remaining_iterations=remaining_iterations,
            observations_text=observations_text,
            tool_results_text=tool_results_text,
            tool_guidance=tool_guidance,
            max_iterations_warning=max_iterations_warning,
            available_tools=available_tools
        )

        # Get LLM response
        response = await self.llm_provider.generate(prompt, temperature=0.7)
        
        # Log model response to terminal
        print(f"\n{'='*80}")
        print(f"🤖 Agent Response (Iteration {iterations + 1}/{self.max_iterations})")
        print(f"{'='*80}")
        print(response)
        print(f"{'='*80}\n")
        
        # Check if this is a final answer
        if "Final Answer:" in response or "final answer:" in response.lower():
            # Extract final answer
            final_answer = response.split("Final Answer:")[-1].strip()
            if "final answer:" in response.lower():
                final_answer = response.split("final answer:")[-1].strip()
            
            # Try to parse as JSON (list of issues)
            try:
                # Try to extract JSON array from the response
                json_match = re.search(r'\[.*\]', final_answer, re.DOTALL)
                if json_match:
                    issues = json.loads(json_match.group(0))
                else:
                    # Fallback: create a single issue from the text
                    issues = [{
                        "file": "general",
                        "line": 0,
                        "severity": "info",
                        "message": final_answer,
                        "suggestion": ""
                    }]
            except (json.JSONDecodeError, AttributeError):
                # If parsing fails, create a structured issue from text
                issues = [{
                    "file": "general",
                    "line": 0,
                    "severity": "info",
                    "message": final_answer,
                    "suggestion": ""
                }]
            
            return {
                "identified_issues": issues,
                "metadata": {
                    **state.get("metadata", {}),
                    "agent_status": "completed",
                    "agent_iterations": iterations + 1,
                    "agent_final_response": final_answer
                }
            }
        
        # Check for tool call
        tool_call = self._extract_tool_call(response)
        
        if tool_call:
            # Execute tool
            tool_name = tool_call["tool"]
            tool_input = tool_call["input"]
            
            tool_result = await self._execute_tool(tool_name, tool_input)
            
            # Check if tool execution failed
            # Tools return {"error": None} on success, {"error": "message"} on failure
            # So we need to check if error exists AND is not None/empty
            tool_failed = (
                isinstance(tool_result, dict) 
                and "error" in tool_result 
                and tool_result.get("error") is not None
                and tool_result.get("error") != ""
            )
            if tool_failed:
                # Log tool failure
                logger.warning(
                    f"Tool call failed - Tool: {tool_name}, "
                    f"Input: {json.dumps(tool_input, ensure_ascii=False)}, "
                    f"Error: {tool_result.get('error', 'Unknown error')}"
                )
                # Update failure count
                current_failures = state.get("metadata", {}).get("agent_tool_failures", 0)
                tool_failures = current_failures + 1
            else:
                tool_failures = state.get("metadata", {}).get("agent_tool_failures", 0)
            
            # Create observation with full details
            observation = f"Used {tool_name} with input {json.dumps(tool_input, ensure_ascii=False)}. Result: {json.dumps(tool_result, ensure_ascii=False, indent=2)}"
            
            return {
                "metadata": {
                    **state.get("metadata", {}),
                    "agent_iterations": iterations + 1,
                    "agent_observations": (state.get("metadata", {}).get("agent_observations", []) + [observation]),
                    "agent_tool_results": (state.get("metadata", {}).get("agent_tool_results", []) + [
                        {"tool": tool_name, "input": tool_input, "result": tool_result}
                    ]),
                    "agent_tool_failures": tool_failures,
                    "agent_last_response": response
                }
            }
        else:
            # No tool call, treat as observation/thinking
            observation = f"Agent reasoning: {response[:300]}"
            return {
                "metadata": {
                    **state.get("metadata", {}),
                    "agent_iterations": iterations + 1,
                    "agent_observations": (state.get("metadata", {}).get("agent_observations", []) + [observation]),
                    "agent_last_response": response
                }
            }
    
    def _should_continue(self, state: ReviewState) -> str:
        """Determine if the agent should continue or finish.
        
        Args:
            state: Current state.
        
        Returns:
            "continue" if should continue, "end" if should finish.
        """
        # Check if we have final issues
        if state.get("identified_issues"):
            return "end"
        
        # Check if max iterations reached
        iterations = state.get("metadata", {}).get("agent_iterations", 0)
        if iterations >= self.max_iterations:
            return "end"
        
        # Check if agent explicitly said it's done
        status = state.get("metadata", {}).get("agent_status", "")
        if status == "completed":
            return "end"
        
        return "continue"
    
    def create_workflow(self) -> Any:
        """Create the LangGraph workflow for the ReAct agent.
        
        Returns:
            Compiled LangGraph workflow.
        """
        workflow = StateGraph(ReviewState)
        
        # Add the agent step node
        workflow.add_node("agent", self._agent_step)
        
        # Set entry point
        workflow.set_entry_point("agent")
        
        # Add conditional edge based on should_continue
        workflow.add_conditional_edges(
            "agent",
            self._should_continue,
            {
                "continue": "agent",  # Loop back to agent
                "end": END
            }
        )
        
        return workflow.compile()


def create_react_agent(config: Config) -> Any:
    """Create a ReAct agent workflow.
    
    Args:
        config: Configuration object.
    
    Returns:
        Compiled LangGraph workflow.
    """
    # Initialize LLM provider
    llm_provider = LLMProvider(config.llm)
    
    # Initialize tools with workspace root and asset key from config
    workspace_root = config.system.workspace_root
    asset_key = getattr(config.system, 'asset_key', None)
    tools = [
        FetchRepoMapTool(asset_key=asset_key),
        ReadFileTool(workspace_root=workspace_root)
    ]
    
    # Create agent
    agent = ReActAgent(llm_provider, tools)
    
    # Create and return workflow
    return agent.create_workflow()


async def run_react_agent(
    pr_diff: str,
    config: Config = None,
    lint_errors: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Run the ReAct agent for code review.
    
    Args:
        pr_diff: The raw Git diff string from the PR.
        config: Optional configuration object. If None, uses default config.
        lint_errors: Optional list of linting errors from pre-agent syntax checking.
    
    Returns:
        A dictionary containing the final state with review results.
    """
    if config is None:
        from core.config import Config
        config = Config.load_default()
    
    # Create workflow
    app = create_react_agent(config)
    
    # Initialize state
    initial_state: ReviewState = {
        "pr_diff": pr_diff,
        "repo_map_summary": "",  # Will be fetched by agent via tool
        "focus_files": [],
        "identified_issues": [],
        "lint_errors": lint_errors or [],
        "metadata": {
            "workflow_version": "react_autonomous",
            "config_provider": config.llm.provider,
            "agent_iterations": 0,
            "agent_observations": [],
            "agent_tool_results": [],
            "agent_tool_failures": 0
        }
    }
    
    # Run the workflow
    try:
        final_state = await app.ainvoke(initial_state)
        return final_state
    except Exception as e:
        # Error handling: return error state
        return {
            **initial_state,
            "identified_issues": [{
                "file": "workflow",
                "line": 0,
                "severity": "error",
                "message": f"Agent execution error: {str(e)}",
                "suggestion": "Check agent configuration and dependencies"
            }],
            "metadata": {
                **initial_state.get("metadata", {}),
                "workflow_error": str(e)
            }
        }
