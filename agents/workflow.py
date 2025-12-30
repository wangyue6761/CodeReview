"""Multi-agent workflow for code review using LangGraph.

重构说明：
1. 添加 LangGraph checkpointer 支持（MemorySaver）用于记忆持久化
2. 使用 LangChain 标准工具定义（@tool 装饰器）
3. 使用 llm.bind_tools() 绑定工具到模型
4. 使用 LangGraph ToolNode 执行工具调用

This module implements a dynamic parallel execution workflow with:
- Map-Reduce pattern for intent analysis
- Manager node for task routing
- Parallel expert execution with concurrency control
- Final report generation
"""

import logging
from typing import Dict, Any, List, Optional
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import ToolNode
from langchain_core.messages import HumanMessage
from core.state import ReviewState
from core.llm import LLMProvider
from core.langchain_llm import LangChainLLMAdapter
from core.config import Config
from tools.repo_tools import FetchRepoMapTool
from tools.file_tools import ReadFileTool
from tools.langchain_tools import create_tools_with_context
from agents.nodes.intent_analysis import intent_analysis_node
from agents.nodes.manager import manager_node
from agents.nodes.expert_execution import expert_execution_node
from agents.nodes.reporter import reporter_node

logger = logging.getLogger(__name__)


def create_multi_agent_workflow(
    config: Config,
    enable_checkpointing: bool = False  # 重构说明：默认禁用，因为代码审查工作流通常不需要跨会话持久化
) -> Any:
    """Create the multi-agent workflow graph.
    
    重构说明：
    1. 添加 checkpointer 支持（MemorySaver）用于记忆持久化
    2. 创建 LangChain LLM 适配器，支持 LCEL 语法
    3. 使用标准工具定义（@tool 装饰器）
    4. 使用 llm.bind_tools() 绑定工具（在需要工具调用的节点中）
    
    Workflow structure:
    1. Intent Analysis (Map-Reduce): Analyze each changed file in parallel
    2. Manager: Generate work_list and group by risk_type into expert_tasks
    3. Expert Execution: Parallel execution of expert groups with concurrency control
    4. Reporter: Generate final report from expert results
    
    Args:
        config: Configuration object.
        enable_checkpointing: 是否启用 checkpointer（记忆持久化）。
    
    Returns:
        Compiled LangGraph workflow with checkpointer support.
    """
    # Initialize LLM provider
    llm_provider = LLMProvider(config.llm)
    
    # 重构说明：创建 LangChain LLM 适配器，支持 LCEL 语法
    llm_adapter = LangChainLLMAdapter(llm_provider=llm_provider)
    
    # Initialize tools (保持向后兼容，同时支持新工具)
    workspace_root = config.system.workspace_root
    asset_key = config.system.asset_key
    tools = [
        # FetchRepoMapTool(asset_key=asset_key),
        ReadFileTool(workspace_root=workspace_root)
    ]
    
    # 重构说明：创建 LangChain 标准工具（使用 @tool 装饰器）
    langchain_tools = create_tools_with_context(
        workspace_root=workspace_root,
        asset_key=asset_key
    )
    
    # 重构说明：创建 checkpointer 用于记忆持久化
    # 这是 LangGraph 标准做法，替代 ConversationBufferMemory 等传统 Memory 类
    checkpointer = MemorySaver() if enable_checkpointing else None
    
    # Create workflow graph
    workflow = StateGraph(ReviewState)
    
    # Add nodes
    workflow.add_node("intent_analysis", intent_analysis_node)
    workflow.add_node("manager", manager_node)
    workflow.add_node("expert_execution", expert_execution_node)
    workflow.add_node("reporter", reporter_node)
    
    # 重构说明：添加 ToolNode 用于执行工具调用
    # 这是 LangGraph 标准做法，替代手动解析工具调用
    # 注意：当前工作流中工具调用主要在 expert_execution_node 中手动处理
    # 如果需要，可以添加一个专门的工具执行节点
    # tool_node = ToolNode(langchain_tools)
    # workflow.add_node("tools", tool_node)
    
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
    
    # Compile workflow with checkpointer
    # 重构说明：使用 checkpointer 编译工作流，支持记忆持久化
    compile_kwargs = {}
    if checkpointer:
        compile_kwargs["checkpointer"] = checkpointer
    
    compiled = workflow.compile(**compile_kwargs)
    
    # Wrap nodes to inject dependencies
    return _wrap_workflow_with_dependencies(
        compiled, 
        llm_provider, 
        llm_adapter,
        config, 
        tools,
        langchain_tools
    )


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
    llm_adapter: LangChainLLMAdapter,
    config: Config,
    tools: List[Any],
    langchain_tools: List[Any]
) -> Any:
    """Wrap workflow nodes to inject dependencies (LLM provider, config, tools).
    
    重构说明：
    - 添加 llm_adapter 到 metadata，支持 LCEL 语法
    - 添加 langchain_tools 到 metadata，支持工具绑定
    
    This is a workaround since LangGraph doesn't directly support dependency injection.
    We'll modify the state to include these dependencies in metadata before execution.
    
    Args:
        compiled_graph: Compiled LangGraph workflow.
        llm_provider: LLM provider instance.
        llm_adapter: LangChain LLM adapter instance (for LCEL syntax).
        config: Configuration object.
        tools: List of tool instances (legacy BaseTool).
        langchain_tools: List of LangChain tool instances (using @tool decorator).
    
    Returns:
        Wrapped compiled graph.
    """
    # Store original invoke methods
    original_ainvoke = compiled_graph.ainvoke
    original_invoke = compiled_graph.invoke
    
    async def ainvoke_with_deps(state: ReviewState, **kwargs) -> ReviewState:
        """Invoke workflow with dependencies injected into state.
        
        重构说明：
        - 初始化 messages 字段（如果不存在）
        - 注入 llm_adapter 支持 LCEL 语法
        - 注入 langchain_tools 支持工具绑定
        """
        # 重构说明：初始化 messages 字段（LangGraph 标准）
        if "messages" not in state:
            state["messages"] = []
        
        # Inject dependencies into metadata
        if "metadata" not in state:
            state["metadata"] = {}
        
        state["metadata"]["llm_provider"] = llm_provider
        state["metadata"]["llm_adapter"] = llm_adapter  # 新增：支持 LCEL 语法
        state["metadata"]["config"] = config
        state["metadata"]["tools"] = tools  # 保持向后兼容
        state["metadata"]["langchain_tools"] = langchain_tools  # 新增：LangChain 标准工具
        
        # Call original invoke
        return await original_ainvoke(state, **kwargs)
    
    def invoke_with_deps(state: ReviewState, **kwargs) -> ReviewState:
        """Invoke workflow with dependencies injected into state (sync version)."""
        # 重构说明：初始化 messages 字段（LangGraph 标准）
        if "messages" not in state:
            state["messages"] = []
        
        # Inject dependencies into metadata
        if "metadata" not in state:
            state["metadata"] = {}
        
        state["metadata"]["llm_provider"] = llm_provider
        state["metadata"]["llm_adapter"] = llm_adapter  # 新增：支持 LCEL 语法
        state["metadata"]["config"] = config
        state["metadata"]["tools"] = tools  # 保持向后兼容
        state["metadata"]["langchain_tools"] = langchain_tools  # 新增：LangChain 标准工具
        
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
    # 重构说明：添加 messages 字段初始化（LangGraph 标准）
    initial_state: ReviewState = {
        "messages": [],  # 新增：消息历史（LangGraph 标准）
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
        # 重构说明：如果启用了 checkpointer，需要传入 config 包含 thread_id
        # 为每次运行生成唯一的 thread_id（基于时间戳和文件列表）
        # 注意：当前默认禁用 checkpointer，所以通常不需要传入 config
        # 但如果需要启用，可以取消下面的注释并传入 config
        invoke_kwargs = {}
        
        # 可选：如果启用了 checkpointer，生成 thread_id 并传入 config
        # import hashlib
        # import time
        # thread_id = hashlib.md5(
        #     f"{time.time()}_{','.join(changed_files)}".encode()
        # ).hexdigest()[:16]
        # invoke_kwargs['config'] = {
        #     "configurable": {
        #         "thread_id": thread_id
        #     }
        # }
        
        final_state = await app.ainvoke(initial_state, **invoke_kwargs)
        
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
