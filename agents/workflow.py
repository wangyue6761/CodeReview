"""Multi-agent workflow for code review using LangGraph.

This module implements a dynamic parallel execution workflow with:
- Map-Reduce pattern for intent analysis
- Manager node for task routing
- Parallel expert execution with concurrency control
- Final report generation
"""

import logging
from typing import Dict, Any, List
from langgraph.graph import StateGraph, END
from core.state import ReviewState
from core.llm import LLMProvider
from core.config import Config
from tools.repo_tools import FetchRepoMapTool
from tools.file_tools import ReadFileTool
from agents.nodes.intent_analysis import intent_analysis_node
from agents.nodes.manager import manager_node
from agents.nodes.expert_execution import expert_execution_node
from agents.nodes.reporter import reporter_node

logger = logging.getLogger(__name__)


def create_multi_agent_workflow(config: Config) -> Any:
    """Create the multi-agent workflow graph.
    
    Workflow structure:
    1. Intent Analysis (Map-Reduce): Analyze each changed file in parallel
    2. Manager: Generate work_list and group by risk_type into expert_tasks
    3. Expert Execution: Parallel execution of expert groups with concurrency control
    4. Reporter: Generate final report from expert results
    
    Args:
        config: Configuration object.
    
    Returns:
        Compiled LangGraph workflow.
    """
    # Initialize LLM provider
    llm_provider = LLMProvider(config.llm)
    
    # Initialize tools
    workspace_root = config.system.workspace_root
    asset_key = config.system.asset_key
    tools = [
        FetchRepoMapTool(asset_key=asset_key),
        ReadFileTool(workspace_root=workspace_root)
    ]
    
    # Create workflow graph
    workflow = StateGraph(ReviewState)
    
    # Add nodes
    workflow.add_node("intent_analysis", intent_analysis_node)
    workflow.add_node("manager", manager_node)
    workflow.add_node("expert_execution", expert_execution_node)
    workflow.add_node("reporter", reporter_node)
    
    # Set entry point
    workflow.set_entry_point("intent_analysis")
    
    # Add edges
    # Intent Analysis -> Manager
    workflow.add_edge("intent_analysis", "manager")
    
    # Manager -> Expert Execution or Reporter (conditional)
    workflow.add_conditional_edges(
        "manager",
        route_to_experts,
        {
            "expert_execution": "expert_execution",
            "reporter": "reporter"
        }
    )
    
    # Expert Execution -> Reporter
    workflow.add_edge("expert_execution", "reporter")
    
    # Reporter -> END
    workflow.add_edge("reporter", END)
    
    # Compile workflow
    compiled = workflow.compile()
    
    # Wrap nodes to inject dependencies
    return _wrap_workflow_with_dependencies(compiled, llm_provider, config, tools)


def route_to_experts(state: ReviewState) -> str:
    """Route from manager to expert_execution or reporter.
    
    If work_list is empty, skip to reporter. Otherwise, go to expert_execution.
    
    Args:
        state: Current workflow state.
    
    Returns:
        Next node name: "expert_execution" or "reporter".
    """
    print("\n" + "="*80)
    print("🔀 [路由] route_to_experts - 决策下一步")
    print("="*80)
    
    work_list = state.get("work_list", [])
    expert_tasks = state.get("expert_tasks", {})
    
    if not work_list or not expert_tasks:
        print("  ⏭️  无任务，跳过专家执行，直接生成报告")
        logger.info("No work list or expert tasks, skipping to reporter")
        return "reporter"
    
    print(f"  ➡️  路由到 expert_execution")
    print(f"     - 专家组数: {len(expert_tasks)}")
    print("="*80)
    logger.info(f"Routing to expert_execution with {len(expert_tasks)} expert groups")
    return "expert_execution"


def _wrap_workflow_with_dependencies(
    compiled_graph: Any,
    llm_provider: LLMProvider,
    config: Config,
    tools: List[Any]
) -> Any:
    """Wrap workflow nodes to inject dependencies (LLM provider, config, tools).
    
    This is a workaround since LangGraph doesn't directly support dependency injection.
    We'll modify the state to include these dependencies in metadata before execution.
    
    Args:
        compiled_graph: Compiled LangGraph workflow.
        llm_provider: LLM provider instance.
        config: Configuration object.
        tools: List of tool instances.
    
    Returns:
        Wrapped compiled graph.
    """
    # Store original invoke methods
    original_ainvoke = compiled_graph.ainvoke
    original_invoke = compiled_graph.invoke
    
    async def ainvoke_with_deps(state: ReviewState, **kwargs) -> ReviewState:
        """Invoke workflow with dependencies injected into state."""
        # Inject dependencies into metadata
        if "metadata" not in state:
            state["metadata"] = {}
        
        state["metadata"]["llm_provider"] = llm_provider
        state["metadata"]["config"] = config
        state["metadata"]["tools"] = tools
        
        # Call original invoke
        return await original_ainvoke(state, **kwargs)
    
    def invoke_with_deps(state: ReviewState, **kwargs) -> ReviewState:
        """Invoke workflow with dependencies injected into state (sync version)."""
        # Inject dependencies into metadata
        if "metadata" not in state:
            state["metadata"] = {}
        
        state["metadata"]["llm_provider"] = llm_provider
        state["metadata"]["config"] = config
        state["metadata"]["tools"] = tools
        
        # Call original invoke
        return original_invoke(state, **kwargs)
    
    # Replace methods
    compiled_graph.ainvoke = ainvoke_with_deps
    compiled_graph.invoke = invoke_with_deps
    
    return compiled_graph


# Map-Reduce wrapper for intent analysis
async def map_intent_analysis(state: ReviewState) -> ReviewState:
    """Map function for intent analysis (processes one file at a time).
    
    This is used with LangGraph's map-reduce capabilities. However, since
    LangGraph's map-reduce might not be directly available, we'll implement
    a custom parallel execution in the intent_analysis_node.
    
    Args:
        state: Current workflow state.
    
    Returns:
        Updated state with file_analyses.
    """
    # For now, we'll process all files in the intent_analysis_node
    # In a full implementation, we could use LangGraph's map capabilities
    # or implement custom parallel execution
    return await intent_analysis_node(state)


async def run_multi_agent_workflow(
    diff_context: str,
    changed_files: List[str],
    config: Config = None,
    lint_errors: List[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Run the multi-agent workflow for code review.
    
    Args:
        diff_context: The raw Git diff string from the PR.
        changed_files: List of file paths that were changed.
        config: Optional configuration object. If None, uses default config.
        lint_errors: Optional list of linting errors from pre-agent syntax checking.
    
    Returns:
        A dictionary containing the final state with review results.
    """
    if config is None:
        from core.config import Config
        config = Config.load_default()
    
    # Create workflow
    app = create_multi_agent_workflow(config)
    
    # Initialize state
    initial_state: ReviewState = {
        "diff_context": diff_context,
        "changed_files": changed_files,
        "file_analyses": [],
        "work_list": [],
        "expert_tasks": {},
        "expert_results": {},
        "confirmed_issues": [],
        "final_report": "",
        "lint_errors": lint_errors or [],
        "metadata": {
            "workflow_version": "multi_agent_parallel",
            "config_provider": config.llm.provider,
            "confidence_threshold": 0.5
        }
    }
    
    # Run the workflow
    print("\n" + "="*80)
    print("🚀 多智能体工作流启动")
    print("="*80)
    print(f"📝 输入:")
    print(f"   - Diff 上下文: {len(diff_context)} 字符")
    print(f"   - 变更文件数: {len(changed_files)}")
    print(f"   - Lint 错误数: {len(lint_errors) if lint_errors else 0}")
    print("="*80)
    
    try:
        final_state = await app.ainvoke(initial_state)
        
        print("\n" + "="*80)
        print("✅ 工作流执行完成")
        print("="*80)
        print(f"📊 最终结果:")
        print(f"   - 确认问题数: {len(final_state.get('confirmed_issues', []))}")
        print(f"   - 报告长度: {len(final_state.get('final_report', ''))} 字符")
        print("="*80)
        
        return final_state
    except Exception as e:
        logger.error(f"Workflow execution error: {e}", exc_info=True)
        # Error handling: return error state
        return {
            **initial_state,
            "confirmed_issues": [],
            "final_report": f"Workflow execution error: {str(e)}",
            "metadata": {
                **initial_state.get("metadata", {}),
                "workflow_error": str(e)
            }
        }
