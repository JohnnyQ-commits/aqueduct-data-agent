"""Skill 注册中心 — 全局查找所有已注册的 Skill。

Skill 通过 `@register_skill` 装饰器注册。
使用 `get_skill(name)` 按名称获取 Skill 实例。
"""

from __future__ import annotations

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
