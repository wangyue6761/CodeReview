"""专家分析子图。
使用 LangGraph 子图模式实现专家智能体的工具调用循环。
"""

import logging
from typing import List, Optional, Any
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from core.state import RiskItem, ExpertState
from core.langchain_llm import LangChainLLMAdapter
from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


def create_langchain_tools(
    workspace_root: Optional[str] = None,
    asset_key: Optional[str] = None
) -> List[BaseTool]:
    """创建 LangChain 工具列表。
    
    统一使用 langchain_tools.create_tools_with_context 创建标准工具。
    
    Args:
        workspace_root: 工作区根目录（用于工具上下文）。
        asset_key: 仓库映射的资产键（用于 fetch_repo_map）。
    
    Returns:
        LangChain 工具列表：fetch_repo_map, read_file, run_grep。
    """
    from tools.langchain_tools import create_tools_with_context
    from pathlib import Path
    
    if workspace_root:
        workspace_path = Path(workspace_root)
        return create_tools_with_context(
            workspace_root=workspace_path,
            asset_key=asset_key
        )
    else:
        # 如果没有 workspace_root，仍然创建工具（使用默认值）
        return create_tools_with_context(
            workspace_root=None,
            asset_key=asset_key
        )


def tools_condition(state: ExpertState) -> str:
    """条件路由函数：根据最后一条消息是否包含工具调用来决定路由。
    
    Args:
        state: 专家子图状态。
    
    Returns:
        "tools" 如果最后一条消息包含工具调用，否则 "end"。
    """
    messages = state.get("messages", [])
    if not messages:
        return "end"
    
    last_message = messages[-1]
    # 检查最后一条消息是否包含工具调用
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    
    return "end"


def build_expert_graph(
    llm: LangChainLLMAdapter,
    tools: List[BaseTool],
    system_prompt: str
) -> Any:
    """构建专家分析子图。
    
    子图结构：
    1. reasoner 节点：调用 LLM 进行分析
    2. tools 节点：执行工具调用（如果 LLM 返回工具调用）
    3. 条件路由：根据是否有工具调用决定继续或结束
    
    Args:
        llm: LangChain LLM 适配器。
        tools: LangChain 工具列表。
        system_prompt: 系统提示词模板。
    
    Returns:
        编译后的 LangGraph 子图。
    """
    # 绑定工具到 LLM
    llm_with_tools = llm.bind_tools(tools)
    
    # 创建工具节点
    tool_node = ToolNode(tools)
    
    # 定义 reasoner 节点（异步）
    async def reasoner(state: ExpertState) -> ExpertState:
        """推理节点：调用 LLM 进行分析。
        
        如果是第一轮（messages 为空），根据 risk_context 渲染 System Prompt。
        否则直接调用 LLM。
        """
        messages = state.get("messages", [])
        risk_context = state.get("risk_context")
        
        # 如果是第一轮，添加系统提示和初始用户消息
        if not messages:
            system_msg = SystemMessage(content=system_prompt)
            user_msg = HumanMessage(
                content=f"""请分析以下风险项：

                风险类型: {risk_context.risk_type.value}
                文件路径: {risk_context.file_path}
                行号范围: {risk_context.line_number[0]}:{risk_context.line_number[1]}
                描述: {risk_context.description}

                如果需要更多信息，请调用工具。分析完成后，请输出最终的 JSON 结果。"""
            )
            new_messages = [system_msg, user_msg]
        else:
            new_messages = messages
        
        # 调用 LLM（异步）
        response = await llm_with_tools.ainvoke(new_messages)
        
        return {
            "messages": [response]
        }
    
    # 构建图
    graph = StateGraph(ExpertState)
    
    # 添加节点
    graph.add_node("reasoner", reasoner)
    graph.add_node("tools", tool_node)
    
    # 设置入口点
    graph.set_entry_point("reasoner")
    
    # 添加条件边
    graph.add_conditional_edges(
        "reasoner",
        tools_condition,
        {
            "tools": "tools",
            "end": END
        }
    )
    
    # 工具执行后回到 reasoner
    graph.add_edge("tools", "reasoner")
    
    # 编译图
    return graph.compile()


async def run_expert_analysis(
    graph: Any,
    risk_item: RiskItem,
    system_prompt: str
) -> Optional[dict]:
    """运行专家分析子图。
    
    Args:
        graph: 编译后的专家子图。
        risk_item: 待分析的风险项。
        system_prompt: 系统提示词（用于初始化）。
    
    Returns:
        最终验证结果（JSON 字典），如果失败则返回 None。
    """
    try:
        # 初始化状态
        initial_state: ExpertState = {
            "messages": [],
            "risk_context": risk_item,
            "final_result": None
        }
        
        # 运行子图
        final_state = await graph.ainvoke(initial_state)
        
        # 从最后一条消息中提取 JSON 结果
        messages = final_state.get("messages", [])
        if not messages:
            logger.warning("No messages in final state")
            return None
        
        # 获取最后一条消息的内容
        last_message = messages[-1]
        content = last_message.content if hasattr(last_message, "content") else str(last_message)
        
        # 尝试从内容中提取 JSON
        import json
        import re
        
        # 清理内容：移除代码块标记
        content_clean = content.strip()
        if content_clean.startswith("```json"):
            content_clean = content_clean[7:]
        if content_clean.startswith("```"):
            content_clean = content_clean[3:]
        if content_clean.endswith("```"):
            content_clean = content_clean[:-3]
        content_clean = content_clean.strip()
        
        # 方法1：尝试直接解析整个内容
        try:
            result = json.loads(content_clean)
            if isinstance(result, dict) and "risk_type" in result:
                return result
        except json.JSONDecodeError:
            pass
        
        # 方法2：尝试提取 JSON 对象（更健壮的正则表达式）
        # 匹配嵌套的 JSON 对象
        json_pattern = r'\{(?:[^{}]|(?:\{[^{}]*\}))*\}'
        matches = re.finditer(json_pattern, content_clean, re.DOTALL)
        for match in matches:
            try:
                result = json.loads(match.group(0))
                if isinstance(result, dict) and ("risk_type" in result or "file_path" in result):
                    return result
            except json.JSONDecodeError:
                continue
        
        # 方法3：尝试查找代码块中的 JSON
        code_block_pattern = r'```(?:json)?\s*\n(.*?)\n```'
        code_block_match = re.search(code_block_pattern, content, re.DOTALL)
        if code_block_match:
            try:
                result = json.loads(code_block_match.group(1).strip())
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass
        
        # 如果都失败了，返回 None
        logger.warning(f"Failed to extract JSON from response: {content[:200]}")
        return None
        
    except Exception as e:
        import traceback
        error_msg = str(e) if str(e) else type(e).__name__
        error_traceback = traceback.format_exception(type(e), e, e.__traceback__)
        logger.error(f"Error running expert analysis: {error_msg}")
        logger.error(f"Traceback:\n{''.join(error_traceback)}")
        return None

