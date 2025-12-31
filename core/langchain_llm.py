"""LangChain LLM 适配器。

将现有的 LLMProvider 包装成 LangChain 的 LLM 接口。
支持 LCEL (LangChain Expression Language) 语法：prompt | llm | parser。
"""

from typing import Any, Optional, List, Dict
from pydantic import PrivateAttr
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from langchain_core.messages.tool import ToolCall
from core.llm import LLMProvider
from core.config import LLMConfig


class LangChainLLMAdapter(BaseChatModel):
    """将 LLMProvider 适配为 LangChain 的 BaseChatModel。
    
    此类将现有的 LLMProvider 包装成 LangChain 兼容的接口。
    允许使用 LCEL 语法：prompt | llm | parser。
    支持工具绑定：llm.bind_tools(tools)。
    """
    
    _llm_provider: LLMProvider = PrivateAttr()
    _bound_tools: Optional[List[BaseTool]] = PrivateAttr(default=None)
    
    def __init__(
        self,
        llm_provider: LLMProvider,
        bound_tools: Optional[List[BaseTool]] = None,
        **kwargs: Any
    ):
        """初始化适配器。"""
        super().__init__(**kwargs)
        self._llm_provider = llm_provider
        self._bound_tools = bound_tools
    
    @property
    def _llm_type(self) -> str:
        """返回 LLM 类型标识符。"""
        return f"adapter_{self._llm_provider.provider}"
    
    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """生成聊天响应（同步版本）。
        
        注意：此方法应该被 _agenerate 覆盖，因为 LLMProvider 是异步的。
        这里提供同步版本是为了兼容性，但实际应该使用异步版本。
        """
        import asyncio
        return asyncio.run(self._agenerate(messages, stop, run_manager, **kwargs))
    
    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """生成聊天响应（异步版本）。
        
        将 LangChain 的 BaseMessage 列表转换为字符串提示，
        调用底层的 LLMProvider.generate() 方法，
        将响应包装为 ChatResult 对象。
        
        如果绑定了工具，会在 prompt 中添加工具描述，并尝试从响应中解析工具调用。
        """
        # 将消息列表转换为提示字符串
        prompt_parts = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                prompt_parts.append(f"System: {msg.content}")
            elif isinstance(msg, HumanMessage):
                prompt_parts.append(f"Human: {msg.content}")
            elif isinstance(msg, AIMessage):
                prompt_parts.append(f"Assistant: {msg.content}")
            elif isinstance(msg, ToolMessage):
                # 工具消息：显示工具调用结果
                prompt_parts.append(f"Tool Result: {msg.content}")
            else:
                prompt_parts.append(str(msg.content))
        
        # 如果绑定了工具，在 prompt 中添加工具描述
        if self._bound_tools:
            tools_description = "\n\nAvailable Tools:\n"
            for tool in self._bound_tools:
                tools_description += f"- {tool.name}: {tool.description}\n"
            prompt_parts.append(f"System: {tools_description}")
            prompt_parts.append("System: You can call tools by outputting JSON in the format: {\"tool\": \"tool_name\", \"input\": {...}}")
        
        prompt = "\n\n".join(prompt_parts)
        
        # 从 kwargs 中提取参数
        temperature = kwargs.get("temperature", 0.7)
        
        # 调用底层的 LLMProvider
        response_text = await self._llm_provider.generate(
            prompt,
            temperature=temperature,
            **{k: v for k, v in kwargs.items() if k != "temperature"}
        )
        
        # 如果绑定了工具，尝试从响应中解析工具调用
        if self._bound_tools:
            tool_calls_dicts = self._parse_tool_calls_from_response(response_text)
            if tool_calls_dicts:
                # 转换为 ToolCall 对象
                tool_calls = [
                    ToolCall(
                        name=tc["name"],
                        args=tc["args"],
                        id=tc["id"]
                    )
                    for tc in tool_calls_dicts
                ]
                # 创建包含工具调用的 AIMessage
                ai_message = AIMessage(
                    content=response_text,
                    tool_calls=tool_calls
                )
            else:
                # 没有工具调用，创建普通 AIMessage
                ai_message = AIMessage(content=response_text)
        else:
            # 没有绑定工具，创建普通 AIMessage
            ai_message = AIMessage(content=response_text)
        
        # 创建 ChatGeneration
        generation = ChatGeneration(message=ai_message)
        
        # 返回 ChatResult
        return ChatResult(generations=[generation])
    
    def _parse_tool_calls_from_response(self, response: str) -> List[Dict[str, Any]]:
        """从 LLM 响应中解析工具调用。
        
        尝试从响应中提取 JSON 格式的工具调用。
        格式: {"tool": "tool_name", "input": {...}}
        """
        import json
        import re
        
        tool_calls = []
        
        # 方法1: 尝试直接解析整个响应
        try:
            parsed = json.loads(response.strip())
            if isinstance(parsed, dict) and "tool" in parsed and "input" in parsed:
                tool_calls.append({
                    "name": parsed["tool"],
                    "args": parsed["input"],
                    "id": f"call_{len(tool_calls)}"
                })
                return tool_calls
        except json.JSONDecodeError:
            pass
        
        # 方法2: 尝试从代码块中提取
        code_block_pattern = r'```(?:json)?\s*\n(.*?)\n```'
        matches = re.finditer(code_block_pattern, response, re.DOTALL)
        for match in matches:
            try:
                parsed = json.loads(match.group(1).strip())
                if isinstance(parsed, dict) and "tool" in parsed and "input" in parsed:
                    tool_calls.append({
                        "name": parsed["tool"],
                        "args": parsed["input"],
                        "id": f"call_{len(tool_calls)}"
                    })
            except json.JSONDecodeError:
                continue
        
        # 方法3: 尝试提取 JSON 对象
        json_pattern = r'\{(?:[^{}]|(?:\{[^{}]*\}))*"tool"(?:[^{}]|(?:\{[^{}]*\}))*"input"(?:[^{}]|(?:\{[^{}]*\}))*\}'
        matches = re.finditer(json_pattern, response, re.DOTALL)
        for match in matches:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict) and "tool" in parsed and "input" in parsed:
                    # 检查是否已存在
                    existing = any(
                        tc.get("name") == parsed["tool"] and 
                        tc.get("args") == parsed["input"]
                        for tc in tool_calls
                    )
                    if not existing:
                        tool_calls.append({
                            "name": parsed["tool"],
                            "args": parsed["input"],
                            "id": f"call_{len(tool_calls)}"
                        })
            except json.JSONDecodeError:
                continue
        
        return tool_calls
    
    def bind_tools(
        self,
        tools: List[BaseTool],
        **kwargs: Any
    ) -> "LangChainLLMAdapter":
        """绑定工具到 LLM。
        
        返回一个新的适配器实例，该实例在生成响应时会包含工具信息。
        
        Args:
            tools: 要绑定的工具列表。
            **kwargs: 其他参数（未使用）。
        
        Returns:
            绑定了工具的新适配器实例。
        """
        # 创建新实例，复制当前配置并绑定工具
        # 直接创建新实例即可，不需要复制其他配置
        return LangChainLLMAdapter(
            llm_provider=self._llm_provider,
            bound_tools=tools
        )
    
    @classmethod
    def from_config(cls, config: LLMConfig) -> "LangChainLLMAdapter":
        """从配置创建适配器实例。"""
        llm_provider = LLMProvider(config)
        return cls(llm_provider=llm_provider)
