"""空 adapter — platform: none 强制离线模式（开源项目的生死线）。"""

from __future__ import annotations

from typing import Any

from .base import PlatformAdapter


class NoneAdapter(PlatformAdapter):
    """零能力 adapter：管道照跑，平台验证步骤全部确定性降级。

    - Phase 1 源表验证 → 跳过实测，仅静态分析
    - Phase 4 SQL 实测 → ValidatorTool 本地校验
    - Phase 5 DQC → 生成 SQL 供用户手工执行
    """

    name = "none"

    def capabilities(self) -> frozenset[str]:
        return frozenset()

    def health_check(self) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "platform": "none",
            "message": "platform: none（离线模式）——未声明任何平台能力，"
            "管道以本地校验降级运行，平台能力是加分项不是前置条件",
        }
