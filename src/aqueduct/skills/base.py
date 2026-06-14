"""Skill 抽象基类。

所有业务 Skill（数据开发、代码审查、质量测试等）
必须实现 BaseSkill。Skill 通过 `@register_skill` 装饰器注册。

Prompt 从 `skills/prompt/*.tpl.md` 模板加载 — 禁止在 Python 中硬编码。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SkillContext:
    """传递给 Skill 的执行上下文。

    包含输入数据、当前工作流状态，以及可选的 LLM 实例。
    """

    input: Any = None
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillResult:
    """任意 Skill 执行的标准结果。"""

    success: bool
    artifacts: list[str] = field(default_factory=list)  # 产出文件路径列表
    data: Any = None
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)  # 附加元数据


class BaseSkill(ABC):
    """所有业务 Skill 的抽象基类。

    每个 Skill 代表一个完整的业务能力：
    需求澄清、方案设计、SQL 开发等。

    子类必须定义 `name`、`description` 和 `execute()`。
    Prompt 从 `prompt_template_path` 指定的 Jinja2 .tpl.md 模板加载。
    """

    name: str = ""  # Skill 唯一标识（注册键）
    description: str = ""  # Skill 描述
    version: str = "1.0.0"  # 版本号
    prompt_template_path: str = ""  # 相对于 skills/prompt/ 的模板路径

    @abstractmethod
    def execute(self, context: SkillContext) -> SkillResult:
        """以给定上下文执行 Skill。

        Args:
            context: 包含输入数据和工作流状态的执行上下文。

        Returns:
            SkillResult，包含产出物列表和状态。
        """

    def load_prompt_template(self, template_dir: Path | None = None, **variables: Any) -> str:
        """加载并渲染 Prompt 模板。

        使用简单的字符串格式化（{变量} 占位符）。
        如需 Jinja2 模板支持，子类可覆盖此方法。

        Args:
            template_dir: Prompt 模板目录。默认 skills/prompt/。
            **variables: 模板渲染变量。

        Returns:
            渲染后的 Prompt 文本。
        """
        if template_dir is None:
            template_dir = Path(__file__).resolve().parent.parent / "skills" / "prompt"

        template_path = template_dir / self.prompt_template_path
        if not template_path.exists():
            raise FileNotFoundError(f"Prompt 模板不存在: {template_path}")

        content = template_path.read_text(encoding="utf-8")
        try:
            return content.format(**variables)
        except KeyError as e:
            raise KeyError(f"模板变量缺失 {e}，位于 {self.prompt_template_path}") from e

    def validate_input(self, context: SkillContext) -> list[str]:
        """执行前校验 Skill 输入。

        Returns:
            错误消息列表。空列表表示校验通过。
        """
        return []

    def __repr__(self) -> str:
        return f"<Skill name={self.name!r} v={self.version}>"
