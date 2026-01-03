"""ZhipuAI compatibility layer.

LangChain's upstream ChatZhipuAI adapter (langchain_community 0.4.1) converts
ToolMessage but does not serialize AIMessage tool_calls back into the request
messages. ZhipuAI validates tool-role messages against prior assistant tool_calls,
so round-2+ requests can fail with:

  {"error":{"code":"1214","message":"messages 参数非法。请检查文档。"}}

This wrapper preserves tool_calls (from AIMessage.additional_kwargs["tool_calls"])
and ensures tool message content is stringified.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_community.chat_models.zhipuai import ChatZhipuAI


def _stringify_tool_content(content: Any) -> str:
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

def _normalize_tool_calls(tool_calls: Any) -> Optional[List[Dict[str, Any]]]:
    """Normalize tool_calls into an OpenAI-like schema expected by ZhipuAI.

    Accepts either:
    - OpenAI-like: [{"id": "...", "type": "function", "function": {"name": "...", "arguments": "..."}}]
    - LangChain simplified: [{"id": "...", "name": "...", "args": {...}, "type": "tool_call"}]
    """
    if not tool_calls:
        return None
    if not isinstance(tool_calls, list):
        return None

    normalized: List[Dict[str, Any]] = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        # Already in OpenAI-like format
        if "function" in tc and isinstance(tc.get("function"), dict) and "id" in tc:
            normalized.append(tc)
            continue
        tc_id = tc.get("id")
        name = tc.get("name")
        args = tc.get("args")
        if not isinstance(tc_id, str) or not isinstance(name, str):
            continue
        if isinstance(args, str):
            arguments = args
        else:
            try:
                arguments = json.dumps(args if args is not None else {}, ensure_ascii=False, default=str)
            except Exception:
                arguments = "{}"
        normalized.append(
            {
                "id": tc_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        )

    return normalized or None


class ChatZhipuAICompat(ChatZhipuAI):
    """Patch ChatZhipuAI message serialization for tool-calling loops."""

    def _create_message_dicts(  # type: ignore[override]
        self, messages: List[BaseMessage], stop: Optional[List[str]]
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        # ZhipuAI validates message sequences. In LangGraph tool loops, state history may
        # contain only assistant/tool messages unless we explicitly include a user turn.
        # Ensure there is at least one user message early in the sequence.
        has_user = any(isinstance(m, HumanMessage) for m in messages)
        if not has_user:
            sys_tail = 0
            for m in messages:
                if isinstance(m, SystemMessage):
                    sys_tail += 1
                else:
                    break
            messages = [
                *messages[:sys_tail],
                HumanMessage(content="继续。"),
                *messages[sys_tail:],
            ]

        params = self._default_params
        if stop is not None:
            params["stop"] = stop

        message_dicts: List[Dict[str, Any]] = []
        tool_name_by_id: Dict[str, str] = {}
        # Build mapping so ToolMessage.name can be filled if missing.
        for message in messages:
            if not isinstance(message, AIMessage):
                continue
            tool_calls = (message.additional_kwargs or {}).get("tool_calls") or _normalize_tool_calls(
                getattr(message, "tool_calls", None)
            )
            if not isinstance(tool_calls, list):
                continue
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                tc_id = tc.get("id")
                fn = tc.get("function")
                tc_name = None
                if isinstance(fn, dict):
                    tc_name = fn.get("name")
                tc_name = tc_name or tc.get("name")
                if isinstance(tc_id, str) and isinstance(tc_name, str) and tc_id and tc_name:
                    tool_name_by_id[tc_id] = tc_name

        for message in messages:
            if isinstance(message, ChatMessage):
                message_dicts.append({"role": message.role, "content": message.content})
                continue
            if isinstance(message, SystemMessage):
                message_dicts.append({"role": "system", "content": message.content})
                continue
            if isinstance(message, HumanMessage):
                message_dicts.append({"role": "user", "content": message.content})
                continue
            if isinstance(message, AIMessage):
                d: Dict[str, Any] = {"role": "assistant", "content": message.content}
                raw_tool_calls = (message.additional_kwargs or {}).get("tool_calls")
                if raw_tool_calls:
                    d["tool_calls"] = raw_tool_calls
                else:
                    # Some integrations populate `AIMessage.tool_calls` but not `additional_kwargs`.
                    normalized = _normalize_tool_calls(getattr(message, "tool_calls", None))
                    if normalized:
                        d["tool_calls"] = normalized
                message_dicts.append(d)
                continue
            if isinstance(message, ToolMessage):
                name = message.name or (message.additional_kwargs or {}).get("name")
                if not name:
                    name = tool_name_by_id.get(message.tool_call_id)
                d = {
                    "role": "tool",
                    "content": _stringify_tool_content(message.content),
                    "tool_call_id": message.tool_call_id,
                }
                if name:
                    d["name"] = name
                message_dicts.append(d)
                continue
            raise TypeError(f"Got unknown type '{message.__class__.__name__}'.")

        return message_dicts, params
