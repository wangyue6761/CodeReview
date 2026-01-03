"""专家分析子图。
使用 LangGraph 子图模式实现专家智能体的工具调用循环。
"""

import logging
import os
import json
from typing import List, Optional, Any, Dict
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage, BaseMessage, AIMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.language_models import BaseChatModel
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from core.state import RiskItem, ExpertState
from core.config import Config
from langchain_core.tools import BaseTool
from agents.prompts import render_prompt_template
from util.json_utils import extract_json_from_text

logger = logging.getLogger(__name__)

def _log_http_error_details(err: Exception, *, max_body_chars: int = 4000) -> None:
    """Best-effort logging for provider HTTP errors (e.g. httpx.HTTPStatusError)."""
    try:
        resp = getattr(err, "response", None)
        req = getattr(err, "request", None)
        if resp is None:
            return

        status = getattr(resp, "status_code", None)
        url = None
        try:
            url = str(getattr(req, "url", None) or getattr(resp, "url", None))
        except Exception:
            url = None

        body = ""
        try:
            body = getattr(resp, "text", "") or ""
        except Exception:
            body = ""

        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        if not isinstance(body, str):
            body = str(body)

        body = body.strip()
        if len(body) > max_body_chars:
            body = body[:max_body_chars] + "\n...[truncated]..."

        logger.error(f"LLM HTTP error details: status={status} url={url}")
        if body:
            logger.error(f"LLM HTTP error response body:\n{body}")
        else:
            logger.error("LLM HTTP error response body: <empty>")
    except Exception:
        # Never let diagnostics crash the workflow
        return


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
    llm: BaseChatModel,
    tools: List[BaseTool],
    config: Optional[Config] = None,
) -> Any:
    """构建专家分析子图。
    
    子图结构：
    1. reasoner 节点：调用 LLM 进行分析
    2. tools 节点：执行工具调用（如果 LLM 返回工具调用）
    3. 条件路由：根据是否有工具调用决定继续或结束
    
    Args:
        llm: LangChain 标准 ChatModel。
        tools: LangChain 工具列表。
        config: 配置对象（可选），用于获取最大轮次限制。
    
    Returns:
        编译后的 LangGraph 子图。
    """
    # 绑定工具到 LLM
    llm_with_tools = llm.bind_tools(tools)
    
    # 创建工具节点
    tool_node = ToolNode(tools)
    
    # 创建 Pydantic 解析器
    parser = PydanticOutputParser(pydantic_object=RiskItem)
    format_instructions = parser.get_format_instructions()
    
    # 格式化可用工具描述
    tool_descriptions = []
    for tool in tools:
        desc = getattr(tool, 'description', f'Tool: {tool.name}')
        tool_descriptions.append(f"- **{tool.name}**: {desc}")
    available_tools_text = "\n".join(tool_descriptions)

    def _truncate_text(s: str, max_chars: int) -> str:
        if max_chars <= 0:
            return ""
        if s is None:
            return ""
        if len(s) <= max_chars:
            return s
        return s[:max_chars] + "\n...[truncated]..."

    def _stringify_content(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, bytes):
            return content.decode("utf-8", errors="replace")
        try:
            return json.dumps(content, ensure_ascii=False, default=str)
        except Exception:
            return str(content)

    def _copy_with_content(msg: BaseMessage, content: str) -> BaseMessage:
        # langchain_core messages are pydantic models (v1/v2 depending on version)
        if hasattr(msg, "model_copy"):
            return msg.model_copy(update={"content": content})
        if hasattr(msg, "copy"):
            return msg.copy(update={"content": content})  # type: ignore[attr-defined]
        # Fallback: best-effort reconstruct common types
        if isinstance(msg, ToolMessage):
            return ToolMessage(content=content, tool_call_id=getattr(msg, "tool_call_id", ""))
        if isinstance(msg, HumanMessage):
            return HumanMessage(content=content)
        if isinstance(msg, SystemMessage):
            return SystemMessage(content=content)
        return msg

    def _shrink_history(messages: List[BaseMessage]) -> List[BaseMessage]:
        """Hard budget for LLM context: cap history length + truncate tool payloads.

        Notes:
        - Tool results can be very large; truncating them reduces context blowups.
        - Some tool runners may attach non-string content; we stringify to keep request payloads valid.
        """
        try:
            max_history = int(os.environ.get("EXPERT_MAX_HISTORY_MESSAGES", "16"))
        except Exception:
            max_history = 16
        try:
            max_total_chars = int(os.environ.get("EXPERT_MAX_TOTAL_CHARS", "80000"))
        except Exception:
            max_total_chars = 80000
        try:
            max_tool_chars = int(os.environ.get("EXPERT_MAX_TOOL_CHARS", "6000"))
        except Exception:
            max_tool_chars = 6000

        max_history = max(1, max_history)
        max_total_chars = max(10_000, max_total_chars)
        max_tool_chars = max(500, max_tool_chars)

        # 1) keep only the most recent messages (history is append-only via add_messages)
        tail = messages[-max_history:]

        # 2) truncate oversized tool messages (biggest offender)
        clipped: List[BaseMessage] = []
        for m in tail:
            c = getattr(m, "content", "")
            if isinstance(m, ToolMessage):
                # Ensure tool message content is string-serializable (provider adapters vary in strictness).
                c_str = _stringify_content(c)
                if len(c_str) > max_tool_chars:
                    c_str = _truncate_text(c_str, max_tool_chars)
                clipped.append(_copy_with_content(m, c_str))
                continue
            clipped.append(m)

        # 3) enforce total budget by dropping oldest remaining messages
        def total_chars(msgs: List[BaseMessage]) -> int:
            n = 0
            for x in msgs:
                cc = getattr(x, "content", "")
                if isinstance(cc, str):
                    n += len(cc)
            return n

        while len(clipped) > 1 and total_chars(clipped) > max_total_chars:
            clipped.pop(0)
        return clipped
    
    async def handle_circuit_breaker(
        messages: List[BaseMessage],
        current_round: int,
        max_rounds: int,
        raw_llm: BaseChatModel,
        format_instructions: str,
        risk_context: RiskItem
    ) -> Optional[Dict[str, Any]]:
        """处理轮次熔断逻辑（物理熔断版本）。
        
        Args:
            messages: 当前消息列表。
            max_rounds: 最大轮次限制。
            raw_llm: 未绑定工具的原始模型实例。
            format_instructions: Pydantic Parser 的格式说明。
            risk_context: 风险项上下文。
        
        Returns:
            如果触发熔断，返回包含强制结束响应的状态；否则返回 None。
        """
        if current_round >= max_rounds:
            # 触发熔断：构造强制结束提示
            logger.warning(f"Circuit breaker triggered: {current_round} rounds >= {max_rounds} max rounds")
            
            # 构建完整的强制停止提示词
            force_stop_content = f"""⚠️ **紧急停止：分析轮次已达上限 ({current_round} >= {max_rounds})**

                **请立即停止调用任何工具！直接最终分析！**

                请根据目前已收集到的信息，**直接输出最终的 JSON 结果**。
                即使信息不完整，也要基于现有证据给出判断。

                ## 当前任务锚点
                风险类型: {risk_context.risk_type.value}
                文件路径: {risk_context.file_path}
                行号范围: {risk_context.line_number[0]}:{risk_context.line_number[1]}
                描述: {risk_context.description}

                ## 输出格式要求（必须严格遵守）
                {format_instructions}

                **重要：你必须输出一个有效的 JSON 对象，格式必须完全符合上述要求。不要输出任何解释性文字，只输出 JSON。**"""
            
            force_stop_msg = SystemMessage(content=force_stop_content)
            
            # 执行强制推理：构造消息列表
            # TODO: 强制兜底回复，不传入历史对话，因为传入历史对话模型会继续问工具，因此直接兜底回复
            # Some providers require a user turn; keep it minimal to avoid 400s.
            new_messages = [
                force_stop_msg,
                HumanMessage(content="请直接输出最终 JSON（不要调用工具，不要输出解释）。"),
            ]
            
            # 关键：使用原始 LLM（未绑定工具），物理上切断工具调用路径
            try:
                response = await raw_llm.ainvoke(new_messages)
            except Exception as e:
                # If provider rejects the request (e.g., 400) or is unavailable, return a minimal valid JSON
                # so the pipeline can proceed with a conservative, low-confidence result.
                logger.error(f"Circuit breaker fallback LLM call failed: {type(e).__name__}: {e}")
                fallback_json = {
                    "risk_type": risk_context.risk_type.value,
                    "file_path": risk_context.file_path,
                    "line_number": [risk_context.line_number[0], risk_context.line_number[1]],
                    "description": risk_context.description,
                    "confidence": 0.0,
                    "severity": "info",
                    "suggestion": None,
                }
                return {"messages": [SystemMessage(content=json.dumps(fallback_json, ensure_ascii=False))]}
            
            if hasattr(response, "tool_calls"):
                response.tool_calls = []
            
            return {
                "messages": [response]
            }
        
        return None
    
    def build_system_message(
        risk_context: RiskItem,
        risk_type_str: str,
        file_content: str
    ) -> SystemMessage:
        """构建系统提示词消息。
        
        Args:
            risk_context: 风险项上下文。
            risk_type_str: 风险类型字符串。
            file_content: 文件完整内容（可选）。
        
        Returns:
            构建好的 SystemMessage。
        """
        # 获取基础系统提示词
        try:
            base_system_prompt = render_prompt_template(
                f"expert_{risk_type_str}",
                risk_type=risk_type_str,
                available_tools=available_tools_text,
                validation_logic_examples=""
            )
        except FileNotFoundError:
            # 回退到通用提示词
            base_system_prompt = render_prompt_template(
                "expert_generic",
                risk_type=risk_type_str,
                available_tools=available_tools_text
            )
        
        # 构建完整的 SystemMessage 内容
        system_content = f"""{base_system_prompt}
            ## 当前任务锚点
            风险类型: {risk_context.risk_type.value}
            文件路径: {risk_context.file_path}
            行号范围: {risk_context.line_number[0]}:{risk_context.line_number[1]}
            描述: {risk_context.description}"""

        if file_content:
            # IMPORTANT: Do not inject full file content into the SystemMessage.
            # It can easily exceed model context (e.g. 260k+ tokens). Provide a focused window.
            try:
                start_line, end_line = int(risk_context.line_number[0]), int(risk_context.line_number[1])
            except Exception:
                start_line, end_line = 1, 1
            window = 200
            lines = file_content.splitlines()
            lo = max(1, start_line - window)
            hi = min(len(lines), end_line + window)
            snippet = "\n".join(f"{i}: {lines[i-1]}" for i in range(lo, hi + 1))

            system_content += f"""
            ## 文件内容（已截取窗口）
            下面仅提供与风险行号相关的局部窗口（{lo}-{hi}）。如需更多上下文，请使用 read_file 工具按需读取（建议限制 max_lines）。

            {snippet}"""

        system_content += f"""
            ## 输出格式要求
            {format_instructions}
            """
        
        return SystemMessage(content=system_content)
    
    # 定义 reasoner 节点（异步）
    async def reasoner(state: ExpertState) -> ExpertState:
        """推理节点：调用 LLM 进行分析。
        
        第一轮动态构建包含完整上下文的 SystemMessage，后续轮次直接使用历史消息。
        包含轮次熔断机制，防止无限循环。
        """
        messages = state.get("messages", [])
        risk_context = state.get("risk_context")
        file_content = state.get("file_content", "")
        risk_type_str = risk_context.risk_type.value
        
        # 计算当前轮次：只统计模型输出轮次（工具消息不计入）。
        current_round = 1 + sum(1 for m in messages if isinstance(m, AIMessage))
        line_start, line_end = risk_context.line_number
        print(f"  🔍 [专家分析] 第 {current_round} 轮 | [{risk_type_str}] {risk_context.file_path}:{line_start}-{line_end}")
        
        # 构建系统提示词
        system_msg = build_system_message(risk_context, risk_type_str, file_content)

        # 检查轮次：如果超过最大轮次，触发物理熔断
        max_rounds = config.system.max_expert_rounds if config else 20
        circuit_breaker_result = await handle_circuit_breaker(
            [*messages], 
            current_round,
            max_rounds,
            llm,  # 传入原始 LLM（未绑定工具）
            format_instructions,  # 传入格式说明
            risk_context  # 传入风险上下文
        )
        if circuit_breaker_result is not None:
            return circuit_breaker_result
        
        if not messages:
            # 构建初始 UserMessage
            user_msg_content = "请分析上述风险项。如果需要更多信息，请调用工具。分析完成后，请输出最终的 JSON 结果。"
            user_msg = HumanMessage(content=user_msg_content)
            new_messages = [system_msg, user_msg]
        else:
            # 后续轮次：直接使用历史消息（SystemMessage 已在第一轮添加）
            new_messages = [system_msg, *_shrink_history([*messages])]
        
        # 调用 LLM（异步）
        try:
            response = await llm_with_tools.ainvoke(new_messages)
        except Exception as e:
            # Print 400 body (or any HTTP error body) to quickly diagnose request format/size/tool support issues.
            _log_http_error_details(e)
            raise
        
        # Ensure the initial user turn becomes part of state history, so subsequent rounds
        # have a valid dialogue sequence (some providers validate this strictly).
        if not messages:
            return {"messages": [user_msg, response]}
        return {"messages": [response]}
    
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
    diff_context: Optional[str] = None,
    file_content: Optional[str] = None,
    recursion_limit: Optional[int] = None,
) -> Optional[dict]:
    """运行专家分析子图。
    
    Args:
        graph: 编译后的专家子图。
        risk_item: 待分析的风险项。
        risk_type_str: 风险类型字符串（用于渲染提示词模板）。
        diff_context: 文件的 diff 上下文（可选）。
        file_content: 文件的完整内容（可选）。
    
    Returns:
        包含 'result' 和 'messages' 的字典，如果失败则返回 None。
        - result: 最终验证结果（RiskItem 对象）
        - messages: 对话历史（消息列表）
    """
    try:
        # 创建 Pydantic 解析器
        parser = PydanticOutputParser(pydantic_object=RiskItem)
        
        # 初始化状态
        initial_state: ExpertState = {
            "messages": [],
            "risk_context": risk_item,
            "final_result": None,
            "diff_context": diff_context,
            "file_content": file_content
        }
        
        # 运行子图
        invoke_kwargs: Dict[str, Any] = {}
        if recursion_limit is not None:
            invoke_kwargs["config"] = {"recursion_limit": int(recursion_limit)}
        final_state = await graph.ainvoke(initial_state, **invoke_kwargs)
        
        # 从消息中提取最后一条消息的文本内容
        messages = final_state.get("messages", [])
        if not messages:
            logger.warning("No messages in final state")
            return None
        
        # 获取最后一条消息的文本内容
        last_message = messages[-1]
        response_text = last_message.content if hasattr(last_message, "content") else str(last_message)
        
        # 从响应文本中提取 JSON
        json_text = extract_json_from_text(response_text)
        if not json_text:
            logger.warning("Could not extract JSON from response")
            logger.warning(f"Response text (first 500 chars): {response_text[:500]}")
            return None
        
        # 使用 PydanticOutputParser 解析提取的 JSON
        try:
            result: RiskItem = parser.parse(json_text)
        except Exception as e:
            logger.warning(f"PydanticOutputParser failed to parse extracted JSON: {e}")
            logger.warning(f"Extracted JSON (first 500 chars): {json_text[:500]}")
            logger.warning(f"Original response (first 500 chars): {response_text[:500]}")
            return None
        
        return {
            "result": result,
            "messages": messages
        }
        
    except Exception as e:
        import traceback
        error_msg = str(e) if str(e) else type(e).__name__
        error_traceback = traceback.format_exception(type(e), e, e.__traceback__)
        logger.error(f"Error running expert analysis: {error_msg}")
        logger.error(f"Traceback:\n{''.join(error_traceback)}")
        return None
