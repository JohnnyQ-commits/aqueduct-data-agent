"""Skills layer — business logic plugins.

导入所有 Skill 模块以触发 @register_skill 装饰器注册。
每个导入独立 try/except，避免单个 Skill 加载失败导致整个 Skills 层不可用。
"""

from __future__ import annotations

import importlib
import logging

logger = logging.getLogger(__name__)

_SKILL_MODULES = [
    "code_review",
    "ddl_generate",
    "design_scheme",
    "dqc_quality",
    "report_delivery",
    "requirement_clarify",
    "sql_develop",
]

for _mod_name in _SKILL_MODULES:
    try:
        importlib.import_module(f".{_mod_name}", package=__name__)
    except Exception:
        logger.warning("Skill 模块加载失败: %s", _mod_name, exc_info=True)

# 附属 Skill（extra 目录）
try:
    from .extra import productivity_board  # noqa: F401
except Exception:
    logger.warning("附属 Skill 加载失败: productivity_board", exc_info=True)
