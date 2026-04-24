"""
Shared LLM provider abstraction for meter_agent.

Usage:
    from llm import get_provider, LLMResponse, ToolCall, LLMRateLimitError
    from llm.registry import MODEL_CATALOG, get_cheap_model

Each sub-agent adds its parent directory to sys.path before importing:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""

from .base import LLMProvider, LLMResponse, LLMRateLimitError, ToolCall
from .registry import MODEL_CATALOG, get_provider_name, get_cheap_model
from .factory import get_provider

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "LLMRateLimitError",
    "ToolCall",
    "MODEL_CATALOG",
    "get_provider_name",
    "get_cheap_model",
    "get_provider",
]
