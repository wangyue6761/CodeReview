"""Expert graph runtime helpers.

This module keeps the main LangGraph wiring in `agents/expert_graph.py` small by
moving budget control, prompt construction, and history shrinking logic here.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from agents.prompts import render_prompt_template
from core.config import Config
from core.state import ExpertState, RiskItem
from util.console_utils import vprint

logger = logging.getLogger(__name__)

def log_http_error_details(err: Exception, *, max_body_chars: int = 4000) -> None:
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
        return


@dataclass(frozen=True)
class ExpertGraphRuntime:
    llm_raw: BaseChatModel
    llm_for_reasoner: BaseChatModel
    config: Optional[Config]
    tools_enabled: bool
    available_tools_text: str
    format_instructions: str

    async def reasoner(self, state: ExpertState) -> ExpertState:
        """推理节点：调用 LLM 进行分析。"""
        messages = state.get("messages", [])
        risk_context = state.get("risk_context")
        file_content = state.get("file_content", "") or ""
        diff_context = state.get("diff_context", "") or ""
        risk_type_str = risk_context.risk_type.value

        current_round = 1 + sum(1 for m in messages if isinstance(m, AIMessage))
        line_start, line_end = risk_context.line_number
        # Keep terminal clean by default: per-round expert logs are noisy in benchmarks.
        # Enable by setting CR_VERBOSE=1.
        vprint(f"  🔍 [专家分析] 第 {current_round} 轮 | [{risk_type_str}] {risk_context.file_path}:{line_start}-{line_end}")

        system_msg = self.build_system_message(risk_context, risk_type_str, file_content, diff_context)

        max_rounds = self.config.system.max_expert_rounds if self.config else 20
        circuit_breaker_result = await self.handle_circuit_breaker(
            [*messages],
            current_round,
            max_rounds,
            risk_context,
        )
        if circuit_breaker_result is not None:
            return circuit_breaker_result

        max_tool_calls = self.config.system.max_expert_tool_calls if self.config else 6
        tool_budget_result = await self.handle_tool_budget([*messages], int(max_tool_calls), risk_context)
        if tool_budget_result is not None:
            return tool_budget_result

        if not messages:
            user_msg = HumanMessage(
                content="请分析上述风险项。如果需要更多信息，请调用工具。分析完成后，请输出最终的 JSON 结果。"
            )
            new_messages = [system_msg, user_msg]
        else:
            new_messages = [system_msg, *self.shrink_history([*messages])]

        try:
            response = await self.llm_for_reasoner.ainvoke(new_messages)
        except Exception as e:
            log_http_error_details(e)
            raise

        if not messages:
            return {"messages": [user_msg, response]}
        return {"messages": [response]}


    def _truncate_text(self, s: str, max_chars: int) -> str:
        if max_chars <= 0:
            return ""
        if s is None:
            return ""
        if len(s) <= max_chars:
            return s
        return s[:max_chars] + "\n...[truncated]..."

    def _stringify_content(self, content: Any) -> str:
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

    def _copy_with_content(self, msg: BaseMessage, content: str) -> BaseMessage:
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

    def shrink_history(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        """Hard budget for LLM context: cap history length + truncate oversized payloads.

        Notes:
        - Tool results can be very large; truncating them reduces context blowups.
        - Keep message ordering valid: never start with ToolMessage; include the assistant tool-call
          message that precedes trailing ToolMessage blocks when possible.
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
        try:
            max_ai_chars = int(os.environ.get("EXPERT_MAX_AI_CHARS", "12000"))
        except Exception:
            max_ai_chars = 12000

        max_history = max(1, max_history)
        max_total_chars = max(10_000, max_total_chars)
        max_tool_chars = max(500, max_tool_chars)
        max_ai_chars = max(500, max_ai_chars)

        if not messages:
            return []

        collected: List[BaseMessage] = []
        idx = len(messages) - 1
        need_prev_for_tool = False
        while idx >= 0 and (len(collected) < max_history or need_prev_for_tool):
            m = messages[idx]
            collected.append(m)
            need_prev_for_tool = isinstance(m, ToolMessage)
            idx -= 1
        collected.reverse()

        while collected and isinstance(collected[0], ToolMessage):
            collected.pop(0)

        if collected and not any(isinstance(m, HumanMessage) for m in collected):
            for m in reversed(messages[: max(0, idx + 1)]):
                if isinstance(m, HumanMessage):
                    collected.insert(0, m)
                    break

        clipped: List[BaseMessage] = []
        for m in collected:
            c = getattr(m, "content", "")
            if isinstance(m, ToolMessage):
                c_str = self._stringify_content(c)
                if len(c_str) > max_tool_chars:
                    c_str = self._truncate_text(c_str, max_tool_chars)
                clipped.append(self._copy_with_content(m, c_str))
                continue
            if isinstance(m, AIMessage):
                c_str = self._stringify_content(c)
                if len(c_str) > max_ai_chars:
                    c_str = self._truncate_text(c_str, max_ai_chars)
                clipped.append(self._copy_with_content(m, c_str))
                continue
            clipped.append(m)

        def total_chars(msgs: List[BaseMessage]) -> int:
            n = 0
            for x in msgs:
                cc = getattr(x, "content", "")
                if isinstance(cc, str):
                    n += len(cc)
            return n

        while len(clipped) > 1 and total_chars(clipped) > max_total_chars:
            clipped.pop(0)
            while clipped and isinstance(clipped[0], ToolMessage):
                clipped.pop(0)
        return clipped

    def build_evidence_digest(self, messages: List[BaseMessage]) -> str:
        """Build a plain-text digest of recent evidence, avoiding ToolMessage roles in requests."""
        try:
            max_digest_chars = int(os.environ.get("EXPERT_MAX_EVIDENCE_DIGEST_CHARS", "16000"))
        except Exception:
            max_digest_chars = 16000
        max_digest_chars = max(1000, max_digest_chars)

        tool_id_to_name: Dict[str, str] = {}
        for m in messages:
            if isinstance(m, AIMessage):
                for tool_call in getattr(m, "tool_calls", []) or []:
                    if not isinstance(tool_call, dict):
                        continue
                    tool_id = tool_call.get("id") or tool_call.get("tool_call_id")
                    name = tool_call.get("name")
                    if isinstance(tool_id, str) and isinstance(name, str) and tool_id:
                        tool_id_to_name[tool_id] = name

        parts: List[str] = []
        used = 0
        for m in reversed(messages):
            if used >= max_digest_chars:
                break
            if isinstance(m, ToolMessage):
                tool_call_id = getattr(m, "tool_call_id", "") or ""
                tool_name = tool_id_to_name.get(tool_call_id, "tool")
                content = self._truncate_text(self._stringify_content(getattr(m, "content", "")), 3000)
                block = f"[TOOL:{tool_name} id={tool_call_id}]\n{content}\n"
            elif isinstance(m, AIMessage):
                content = self._stringify_content(getattr(m, "content", "")).strip()
                if not content:
                    continue
                content = self._truncate_text(content, 3000)
                block = f"[ASSISTANT]\n{content}\n"
            else:
                continue

            if used + len(block) > max_digest_chars and parts:
                break
            parts.append(block)
            used += len(block)

        parts.reverse()
        return "\n".join(parts).strip()

    def build_system_message(
        self,
        risk_context: RiskItem,
        risk_type_str: str,
        file_content: str,
        diff_context: str,
    ) -> SystemMessage:
        """构建系统提示词消息。"""
        prompt_risk_type = (risk_type_str or "").strip()
        try:
            base_system_prompt = render_prompt_template(
                f"expert_{prompt_risk_type}",
                risk_type=prompt_risk_type,
                available_tools=self.available_tools_text,
                validation_logic_examples="",
            )
        except FileNotFoundError:
            base_system_prompt = render_prompt_template(
                "expert_generic",
                risk_type=prompt_risk_type,
                available_tools=self.available_tools_text,
            )

        system_content = f"""{base_system_prompt}
            ## 当前任务锚点
            风险类型: {risk_context.risk_type.value}
            文件路径: {risk_context.file_path}
            行号范围: {risk_context.line_number[0]}:{risk_context.line_number[1]}
            描述: {risk_context.description}"""

        if diff_context:
            try:
                max_diff_chars = int(os.environ.get("EXPERT_MAX_DIFF_CHARS", "12000"))
            except Exception:
                max_diff_chars = 12000
            max_diff_chars = max(1000, max_diff_chars)
            system_content += f"""
            ## Diff 上下文（已截断）
            {self._truncate_text(diff_context, max_diff_chars)}"""

        if file_content:
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
            下面仅提供与风险行号相关的局部窗口（{lo}-{hi}）。如需更多上下文，请优先使用 read_file_snippet 按行号范围读取（建议设置 max_lines 控制输出预算）。

            {snippet}"""

        system_content += f"""
            ## 输出格式要求
            {self.format_instructions}
            """
        return SystemMessage(content=system_content)

    def _count_tool_messages(self, messages: List[BaseMessage]) -> int:
        return sum(1 for m in messages if isinstance(m, ToolMessage))

    def _is_no_signal_tool_result(self, content: str) -> bool:
        if not content:
            return True
        s = content.strip()
        if "Error invoking tool" in s:
            return True
        if "Input should be a valid list" in s:
            return True
        if "Please fix the error and try again" in s:
            return True
        if "Lite-CPG DB not available" in s:
            return True
        if "No matches found" in s:
            return True
        if "\"matches\": []" in s or "'matches': []" in s:
            return True
        if "\"total\": 0" in s or "'total': 0" in s:
            return True
        if "\"error\"" in s and "\"error\": null" not in s and "\"error\": \"\"" not in s:
            return True
        return False

    def _count_recent_no_signal_tools(self, messages: List[BaseMessage], *, window: int) -> int:
        window = max(1, int(window))
        seen = 0
        n = 0
        for m in reversed(messages):
            if not isinstance(m, ToolMessage):
                continue
            seen += 1
            c_str = self._stringify_content(getattr(m, "content", ""))
            if self._is_no_signal_tool_result(c_str):
                n += 1
            if seen >= window:
                break
        return n

    async def handle_circuit_breaker(
        self,
        messages: List[BaseMessage],
        current_round: int,
        max_rounds: int,
        risk_context: RiskItem,
    ) -> Optional[Dict[str, Any]]:
        """处理轮次熔断逻辑（物理熔断版本）。"""
        if current_round <= max_rounds:
            return None

        logger.warning(f"Circuit breaker triggered: {current_round} rounds > {max_rounds} max rounds")
        force_stop_content = f"""⚠️ **紧急停止：分析轮次已达上限 ({current_round} > {max_rounds})**

                **请立即停止调用任何工具！直接最终分析！**

                请根据目前已收集到的信息，**直接输出最终的 JSON 结果**。
                即使信息不完整，也要基于现有证据给出判断。

                ## 当前任务锚点
                风险类型: {risk_context.risk_type.value}
                文件路径: {risk_context.file_path}
                行号范围: {risk_context.line_number[0]}:{risk_context.line_number[1]}
                描述: {risk_context.description}

                ## 输出格式要求（必须严格遵守）
                {self.format_instructions}

                **重要：你必须输出一个有效的 JSON 对象，格式必须完全符合上述要求。不要输出任何解释性文字，只输出 JSON。**"""

        evidence = self.build_evidence_digest(self.shrink_history([*messages]))
        if evidence:
            force_stop_content += f"""

                ## 已有信息摘录（对话/工具输出）
                以下是最近轮次中已获得的关键输出（已截断）。请优先基于这些信息完成最终判断。

                {evidence}"""

        new_messages = [
            SystemMessage(content=force_stop_content),
            HumanMessage(content="请直接输出最终 JSON（不要调用工具，不要输出解释）。"),
        ]

        try:
            response = await self.llm_raw.ainvoke(new_messages)
        except Exception as e:
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
        return {"messages": [response]}

    async def handle_tool_budget(
        self,
        messages: List[BaseMessage],
        max_tool_calls: int,
        risk_context: RiskItem,
    ) -> Optional[Dict[str, Any]]:
        """Stop tool-calling loops early when tool usage becomes unproductive."""
        if max_tool_calls < 0:
            max_tool_calls = 0

        tool_calls_used = self._count_tool_messages(messages)
        try:
            max_no_signal = int(os.environ.get("EXPERT_MAX_CONSECUTIVE_NO_SIGNAL_TOOLS", "5"))
        except Exception:
            max_no_signal = 5
        max_no_signal = max(1, max_no_signal)
        try:
            no_signal_window = int(os.environ.get("EXPERT_NO_SIGNAL_WINDOW", "10"))
        except Exception:
            no_signal_window = 10
        no_signal_window = max(1, no_signal_window)

        if max_tool_calls == 0 and tool_calls_used > 0:
            reason = "工具调用已被禁用"
        elif max_tool_calls > 0 and tool_calls_used >= max_tool_calls:
            reason = f"工具调用次数已达上限 ({tool_calls_used} >= {max_tool_calls})"
        elif self._count_recent_no_signal_tools(messages, window=no_signal_window) >= max_no_signal:
            reason = f"最近 {no_signal_window} 次工具调用中有 {max_no_signal} 次无有效信息"
        else:
            return None

        logger.warning(f"Tool budget stop triggered: {reason}")
        force_stop_content = f"""⚠️ **停止工具调用：{reason}**
            请基于当前已掌握的信息直接完成最终判断并输出 JSON。
            注意：某些结论可以基于语言语义/常识成立，不一定能在仓库中找到“文字证据”。不要继续尝试全仓库搜索。

            ## 当前任务锚点
            风险类型: {risk_context.risk_type.value}
            文件路径: {risk_context.file_path}
            行号范围: {risk_context.line_number[0]}:{risk_context.line_number[1]}
            描述: {risk_context.description}

            ## 输出格式要求（必须严格遵守）
            {self.format_instructions}

            **重要：你必须输出一个有效的 JSON 对象。不要输出任何解释性文字，只输出 JSON。**"""

        evidence = self.build_evidence_digest(self.shrink_history([*messages]))
        if evidence:
            force_stop_content += f"""

            ## 已有信息摘录（对话/工具输出）
            以下是最近轮次中已获得的关键输出（已截断）。请优先基于这些信息完成最终判断。

            {evidence}"""

        new_messages = [
            SystemMessage(content=force_stop_content),
            HumanMessage(content="请直接输出最终 JSON（不要调用工具，不要输出解释）。"),
        ]
        try:
            response = await self.llm_raw.ainvoke(new_messages)
        except Exception as e:
            logger.error(f"Tool budget fallback LLM call failed: {type(e).__name__}: {e}")
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
        return {"messages": [response]}
