"""LLM 工厂函数。

根据配置创建 LangChain 标准 ChatModel。
"""

from typing import Optional
from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel
from langchain_community.chat_models import ChatZhipuAI
from core.config import LLMConfig


def create_chat_model(config: LLMConfig) -> BaseChatModel:
    """根据配置创建 LangChain 标准 ChatModel。
    
    Args:
        config: LLM 配置对象。
    
    Returns:
        LangChain 标准 ChatModel 实例。
    
    Raises:
        ValueError: 不支持的 provider。
    """
    if config.provider == "openai":
        return ChatOpenAI(
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
            temperature=config.temperature
        )
    elif config.provider == "deepseek":
        # DeepSeek 使用 OpenAI 兼容 API
        return ChatOpenAI(
            model=config.model or "deepseek-chat",
            api_key=config.api_key,
            base_url=config.base_url or "https://api.deepseek.com",
            temperature=config.temperature
        )
    elif config.provider == "zhipuai":
        # NOTE: Use a local compatibility wrapper to support multi-turn tool-calling loops.
        # Upstream langchain_community ChatZhipuAI (0.4.1) does not serialize AIMessage.tool_calls
        # back into request messages, which can trigger ZhipuAI "messages 参数非法" on round 2+.
        from core.zhipuai_compat import ChatZhipuAICompat
        return ChatZhipuAICompat(
            model=config.model or "glm-4.6",
            api_key=config.api_key,
            temperature=config.temperature
        )
    else:
        raise ValueError(f"Unsupported provider: {config.provider}")
