"""Skill 注册中心 — 全局查找所有已注册的 Skill。

Skill 通过 `@register_skill` 装饰器注册。
使用 `get_skill(name)` 按名称获取 Skill 实例。
支持 `load_plugins()` 从外部目录动态加载。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from ..exceptions import SkillNotFoundError
from .base import BaseSkill

# 全局 Skill 注册表：名称 → Skill 类
_SKILL_REGISTRY: dict[str, type[BaseSkill]] = {}


def register_skill(skill_cls: type[BaseSkill]) -> type[BaseSkill]:
    """装饰器：将 Skill 类注册到全局注册表。

    用法:
        @register_skill
        class DataDeveloperSkill(BaseSkill):
            name = "data-developer"
            ...

    Args:
        skill_cls: BaseSkill 的子类，必须定义非空的 `name` 属性。

    Returns:
        原类（不变），兼容装饰器链式使用。

    Raises:
        ValueError: name 为空或已存在同名 Skill 时抛出。
    """
    if not skill_cls.name:
        raise ValueError(f"Skill 类 {skill_cls.__name__} 必须定义非空的 `name` 属性。")
    if skill_cls.name in _SKILL_REGISTRY:
        raise ValueError(
            f"Skill '{skill_cls.name}' 已注册。现有: {_SKILL_REGISTRY[skill_cls.name].__name__}"
        )
    _SKILL_REGISTRY[skill_cls.name] = skill_cls
    return skill_cls


def get_skill(name: str) -> BaseSkill:
    """按名称获取 Skill 实例。

    Args:
        name: 已注册的 Skill 名称。

    Returns:
        Skill 类的新实例。

    Raises:
        SkillNotFoundError: 未找到该名称的 Skill 时抛出。
    """
    if name not in _SKILL_REGISTRY:
        raise SkillNotFoundError(
            f"Skill '{name}' 未注册。可用 Skill: {list(_SKILL_REGISTRY.keys())}"
        )
    return _SKILL_REGISTRY[name]()


def list_skills() -> list[str]:
    """返回所有已注册的 Skill 名称列表。"""
    return list(_SKILL_REGISTRY.keys())


def is_skill_registered(name: str) -> bool:
    """检查 Skill 是否已注册。"""
    return name in _SKILL_REGISTRY


def load_plugins(plugin_dir: str | Path) -> list[str]:
    """从外部目录动态加载 Skill。

    扫描指定目录下所有 .py 文件（跳过 __init__.py），
    执行 import 触发 @register_skill 装饰器注册。

    Args:
        plugin_dir: 外部 skill 目录路径

    Returns:
        本次新注册的 skill 名称列表

    Raises:
        FileNotFoundError: 目录不存在
    """
    plugin_dir = Path(plugin_dir)
    if not plugin_dir.is_dir():
        raise FileNotFoundError(f"Plugin directory not found: {plugin_dir}")

    previous_skills = set(_SKILL_REGISTRY.keys())

    for py_file in sorted(plugin_dir.glob("*.py")):
        if py_file.name.startswith("__"):
            continue
        module_name = f"aqueduct_external_skill_{py_file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

    new_skills = set(_SKILL_REGISTRY.keys()) - previous_skills
    return sorted(new_skills)
