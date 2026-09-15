"""bdp 专用 adapter — 清单声明 capability→transport + 传输就绪探针。

与 auto 的分工：auto 探测既有隐式谓词（零行为变化）；bdp 读
``bdp_manifest.json``（pyyaml 非依赖，JSON 清单零依赖等价）声明能力，
再探每个 transport 是否就绪——``dp-cookie-http`` 的探针是「凭证可解析」
（load_dp_env：os.environ 优先，项目 .env 回退）而非 settings 布尔，
比 auto 诚实：凭证缺失时不再声明能力，门禁直接跳过而非执行时报错。

无实现的动词（lineage/task_ops/artifact_search）清单不声明——不谎报。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base import ALL_CAPABILITIES, Capability, PlatformAdapter

logger = logging.getLogger(__name__)

_MANIFEST_PATH = Path(__file__).resolve().parent / "bdp_manifest.json"


def _load_manifest() -> dict[str, Any]:
    """读取 bdp 能力清单（包内数据文件）。"""
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


def _probe_mcp() -> bool:
    """bdp-asset-mcp 就绪 = .mcp.json 至少配置了一个 server（同 auto 谓词）。"""
    try:
        from ..mcp.config import MCPConfig

        return MCPConfig().is_configured()
    except Exception:
        return False


def _probe_dp_cookie_http() -> bool:
    """dp-cookie-http 就绪 = DP_BASE_URL/DP_COOKIE/DP_USER_ID 全部可解析。"""
    try:
        from ..mcp.adapters.dp_client import load_dp_env

        resolved = load_dp_env()
    except Exception:
        return False
    return all(resolved.get(k) for k in ("DP_BASE_URL", "DP_COOKIE", "DP_USER_ID"))


_DEFAULT_PROBES: dict[str, Callable[[], bool]] = {
    "bdp-asset-mcp": _probe_mcp,
    "dp-cookie-http": _probe_dp_cookie_http,
}


class BDPAdapter(PlatformAdapter):
    """清单驱动的 bdp adapter。

    Args:
        manifest: 能力清单 dict（默认读包内 bdp_manifest.json）。
        probes: transport→就绪探针（默认内置注册表；测试注入覆写）。
    """

    def __init__(
        self,
        manifest: dict[str, Any] | None = None,
        probes: dict[str, Callable[[], bool]] | None = None,
    ):
        self._manifest = manifest if manifest is not None else _load_manifest()
        self._probes = {**_DEFAULT_PROBES, **(probes or {})}
        # 未知 transport 立即失败——清单写错不能等到运行时才炸
        for cap, spec in self._manifest["capabilities"].items():
            transport = spec.get("transport")
            if transport not in self._probes:
                raise ValueError(
                    f"清单里 {cap} 的 transport {transport!r} 没有探针注册，"
                    f"已注册: {sorted(self._probes)}"
                )
        # 就绪快照在构造时取（探针应廉价且幂等；运行中不再反复探测）
        self._caps = frozenset(
            cap
            for cap, spec in self._manifest["capabilities"].items()
            if self._probes[spec["transport"]]()
        )

    @property
    def name(self) -> str:  # type: ignore[override]
        return self._manifest.get("platform", "bdp")

    def capabilities(self) -> frozenset[str]:
        return self._caps & ALL_CAPABILITIES

    def health_check(self) -> dict[str, Any]:
        if not self.has_capability(Capability.SQL_EXECUTE):
            return {
                "status": "unavailable",
                "platform": self.name,
                "message": "dp-cookie-http transport 未就绪（DP_* 凭证缺失），跳过执行类体检",
            }
        from ..tools.registry import get_tool

        result = get_tool("executor").execute(action="health_check")
        ok = bool(getattr(result, "success", False))
        return {
            "status": "ok" if ok else "unavailable",
            "platform": self.name,
            "detail": getattr(result, "data", None),
        }
