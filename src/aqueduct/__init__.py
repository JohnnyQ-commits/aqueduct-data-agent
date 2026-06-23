"""Aqueduct — 工业级数据开发 Agent"""

from .core import Aqueduct, AqueductResult
from .exceptions import LLMTimeoutError

__version__ = "0.4.0"
__all__ = ["Aqueduct", "AqueductResult", "LLMTimeoutError", "__version__"]
