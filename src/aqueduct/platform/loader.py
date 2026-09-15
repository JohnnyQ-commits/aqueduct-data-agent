"""adapter 加载器 — 声明解析 + 模块级单例。

adapter 不进 WorkflowState：单例在进程内惰性构建，
规避 _llm_router 式 checkpoint 序列化失败（d4ade7c 教训）。
"""

from __future__ import annotations

import logging

from ..config.settings import Settings, get_settings
from ..exceptions import ConfigError
from .auto import AutoAdapter
from .base import PlatformAdapter
from .none import NoneAdapter

logger = logging.getLogger(__name__)

_adapter: PlatformAdapter | None = None


def load_adapter(settings: Settings) -> PlatformAdapter:
    """按 settings.platform 声明构建 adapter。

    - ``none``：强制离线（即使 MCP 已配置也不得声明任何能力）
    - ``auto``：探测 MCP 配置 + execution_enabled（与既有门控 1:1）
    - ``bdp``：清单驱动（bdp_manifest.json 声明 capability→transport），
      transport 就绪探针判定实际能力——dp-cookie-http 探「凭证可解析」
      （os.environ 优先、.env 回退），比 auto 的 settings 布尔更诚实
    """
    mode = (settings.platform or "auto").strip().lower()
    if mode == "none":
        return NoneAdapter()
    if mode == "bdp":
        from .bdp import BDPAdapter

        return BDPAdapter()
    if mode == "auto":
        try:
            from ..mcp.config import MCPConfig

            mcp_configured = MCPConfig().is_configured()
        except Exception:
            mcp_configured = False
        return AutoAdapter(
            mcp_configured=mcp_configured,
            execution_enabled=settings.execution_enabled is True,
        )
    raise ConfigError(f"无效的 platform 声明: {settings.platform!r}，可选: none / auto / bdp")


def get_platform_adapter() -> PlatformAdapter:
    """进程内单例（惰性构建，首次调用时解析声明）。"""
    global _adapter
    if _adapter is None:
        _adapter = load_adapter(get_settings())
    return _adapter


def reset_platform_adapter() -> None:
    """清空单例（测试用 / 配置热更后强制重解析）。"""
    global _adapter
    _adapter = None


def describe_adapter(adapter: PlatformAdapter) -> str:
    """一行可读描述（管道启动横幅用，无网络调用）。"""
    caps = sorted(adapter.capabilities())
    cap_text = ", ".join(caps) if caps else "无（离线降级运行）"
    return f"{adapter.name}（能力: {cap_text}）"


def log_platform_banner() -> None:
    """管道启动时打印平台能力横幅。"""
    logger.info("[platform] %s", describe_adapter(get_platform_adapter()))
