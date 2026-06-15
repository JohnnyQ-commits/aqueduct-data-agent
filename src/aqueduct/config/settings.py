"""配置管理 — 基于 pydantic-settings 的环境变量配置。

所有配置通过 `AQUEDUCT_` 前缀的环境变量加载，
支持可选的 .env 文件。

用法:
    from aqueduct.config.settings import get_settings

    settings = get_settings()
    print(settings.knowledge_dir)  # 知识目录路径
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# 项目根目录（src/aqueduct/config/settings.py → 上溯 4 级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class Settings(BaseSettings):
    """Aqueduct 应用配置。

    所有字段均可通过环境变量覆盖：
        AQUEDUCT_KNOWLEDGE_DIR=/path/to/knowledge
        AQUEDUCT_LOG_LEVEL=DEBUG
        等。
    """

    model_config = SettingsConfigDict(
        env_prefix="AQUEDUCT_",  # 环境变量前缀
        env_file=".env",  # 可选的 .env 文件
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # === 项目根目录 ===

    project_root: Path = Field(
        default=_PROJECT_ROOT,
        description="项目根目录，其他相对路径基于此解析。",
    )

    # === 目录配置 ===

    knowledge_dir: Path = Field(
        default=Path("knowledge/domains"),
        description="业务域本体 JSON 文件目录。",
    )

    prompt_dir: Path = Field(
        default=Path("src/aqueduct/skills/prompt"),
        description="Skill Prompt 模板目录（.tpl.md 文件）。",
    )

    output_dir: Path = Field(
        default=Path("output"),
        description="生成产物默认输出目录。",
    )

    workspace_dir: Path = Field(
        default=Path("workspace"),
        description="工作目录，存放输入需求文档和全流程输出物。",
    )

    # === 日志配置 ===

    log_level: str = Field(
        default="INFO",
        description="日志级别：DEBUG / INFO / WARNING / ERROR。",
    )

    log_format: str = Field(
        default="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        description="日志消息格式。",
    )

    # === LLM 配置 ===

    default_analysis_model: str = Field(
        default="",
        description="轻量分析任务模型（Haiku 档）。",
    )

    default_medium_model: str = Field(
        default="",
        description="中等生成任务模型（Sonnet/Qwen 档）。",
    )

    default_heavy_model: str = Field(
        default="",
        description="重度生成任务模型（Opus 档）。",
    )

    # === 工作流配置 ===

    max_workflow_steps: int = Field(
        default=20,
        description="工作流 DAG 最大步数。",
    )

    workflow_timeout_seconds: int = Field(
        default=3600,
        description="工作流最大执行时间（秒）。",
    )

    # === 校验配置 ===

    sql_max_file_size_kb: int = Field(
        default=512,
        description="SQL 文件最大尺寸（KB）。",
    )

    allowed_sql_extensions: list[str] = Field(
        default=[".sql"],
        description="允许的 SQL 文件扩展名。",
    )

    @model_validator(mode="after")
    def _resolve_paths(self) -> Settings:
        """将相对路径解析为基于 project_root 的绝对路径。"""
        root = self.project_root
        for field_name in (
            "knowledge_dir",
            "prompt_dir",
            "output_dir",
            "workspace_dir",
        ):
            path: Path = getattr(self, field_name)
            if not path.is_absolute():
                setattr(self, field_name, root / path)
        return self

    @model_validator(mode="after")
    def _warn_empty_models(self) -> Settings:
        """模型 ID 为空时发出警告。"""
        empty = []
        for name in ("default_analysis_model", "default_medium_model", "default_heavy_model"):
            if not getattr(self, name):
                empty.append(name)
        if empty:
            logger.warning(
                "以下 LLM 模型配置为空，将使用 ClaudeLLM 默认值: %s",
                ", ".join(empty),
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取缓存的配置实例。

    调用 `get_settings.cache_clear()` 可重新从环境变量加载。
    """
    return Settings()
