"""Tools layer — atomic execution units.

导入所有 Tool 模块以触发 @register_tool 装饰器注册。
每个模块独立加载，单个工具失败不影响其他工具注册。
"""

from __future__ import annotations

import importlib
import logging

logger = logging.getLogger(__name__)

_TOOL_MODULES = [
    "batch_query",
    "design",
    "dqc",
    "estimator",
    "executor",
    "lineage",
    "productivity",
    "semantic",
    "sync",
    "validator",
]

for _mod_name in _TOOL_MODULES:
    try:
        importlib.import_module(f".{_mod_name}", package=__name__)
    except Exception as e:
        logger.warning("工具模块 '%s' 加载失败（已跳过）: %s", _mod_name, e)
