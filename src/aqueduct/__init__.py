"""Aqueduct — 工业级数据开发 Agent"""

from .core import Aqueduct, AqueductResult
from .exceptions import AqueductError, LLMTimeoutError

__version__ = "0.4.1"
__all__ = ["Aqueduct", "AqueductError", "AqueductResult", "LLMTimeoutError", "__version__"]
