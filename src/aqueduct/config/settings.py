"""配置管理 — 基于 pydantic-settings 的环境变量配置。

所有配置通过 `AQUEDUCT_` 前缀的环境变量加载，
支持可选的 .env 文件。

兼容 Anthropic 官方变量名（ANTHROPIC_DEFAULT_HAIKU_MODEL 等），
后续接入其他厂商时只需在 _FALLBACK_VAR_MAP 中追加映射。

用法:
    from aqueduct.config.settings import get_settings

    settings = get_settings()
    print(settings.knowledge_dir)  # 知识目录路径
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


def _find_project_root() -> Path:
    """查找项目根目录。

    策略：从当前文件向上搜索，找到包含 pyproject.toml 的目录。
    如未找到，回退到固定层级（src/aqueduct/config/settings.py → 上溯 4 级）。
    """
    # 从当前文件向上搜索 pyproject.toml
    current = Path(__file__).resolve().parent
    for _ in range(10):  # 最多向上 10 级
        if (current / "pyproject.toml").exists():
            return current
        parent = current.parent
        if parent == current:  # 到达文件系统根目录
            break
        current = parent
    # 回退：固定层级
    return Path(__file__).resolve().parent.parent.parent.parent


_PROJECT_ROOT = _find_project_root()

# 第三方变量名 → AQUEDUCT_ 字段名映射（兼容 Anthropic 官方变量名）
# 后续接入 Codex / DeepSeek / 千问等厂商时，在此追加映射即可
_FALLBACK_VAR_MAP: dict[str, str] = {
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "default_analysis_model",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "default_medium_model",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "default_heavy_model",
}


class Settings(BaseSettings):
    """Aqueduct 应用配置。

    所有字段均可通过环境变量覆盖：
        AQUEDUCT_KNOWLEDGE_DIR=/path/to/knowledge
        AQUEDUCT_LOG_LEVEL=DEBUG
        等。
    """

    model_config = SettingsConfigDict(
        env_prefix="AQUEDUCT_",  # 环境变量前缀
        env_file=str(_PROJECT_ROOT / ".env"),  # 从项目根目录解析 .env
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
        description="业务域本体目录。支持域目录模式（{domain_id}/domain.json）和扁平模式（{domain_id}.json）。",
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
        default="claude-haiku-4-5-20251001",
        description="轻量分析任务模型（Haiku 档）。可通过环境变量 AQUEDUCT_DEFAULT_ANALYSIS_MODEL 覆盖。",
    )

    default_medium_model: str = Field(
        default="claude-sonnet-4-6-20250514",
        description="中等生成任务模型（Sonnet 档）。可通过环境变量 AQUEDUCT_DEFAULT_MEDIUM_MODEL 覆盖。",
    )

    default_heavy_model: str = Field(
        default="claude-opus-4-7-20250514",
        description="重度生成任务模型（Opus 档）。可通过环境变量 AQUEDUCT_DEFAULT_HEAVY_MODEL 覆盖。",
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

    max_fix_iterations: int = Field(
        default=2,
        description="审查→修复循环最大迭代次数。",
    )

    # === LLM 重试配置 ===

    llm_max_retries: int = Field(
        default=2,
        description="LLM 调用超时后最大重试次数（指数退避）。",
    )

    llm_timeout_seconds: int = Field(
        default=900,
        description="单次 LLM 调用超时时间（秒）。长输出任务（doc_gen/sql_gen）建议 ≥ 900。",
    )

    llm_max_context_tokens: int = Field(
        default=200_000,
        description="LLM 上下文窗口最大 token 数。超过此值触发自动截断。",
    )

    # === 外部 SQL 输入 ===

    external_sql_path: str = Field(
        default="",
        description="外部 SQL 文件路径。非空时 Phase 4 跳过 LLM 生成，直接读取该文件。",
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

    # === 执行配置 ===

    execution_enabled: bool = Field(
        default=True,
        description="是否启用 SQL 执行能力（DQC 验证）。True=有 DP_* 配置就执行，False=强制不执行。",
    )

    execution_timeout_seconds: int = Field(
        default=300,
        description="单条 SQL 执行超时时间（秒）。",
    )

    execution_max_rows: int = Field(
        default=1000,
        description="SQL 执行最大返回行数。",
    )

    def __init__(self, **data):
        """注入第三方环境变量 fallback。

        pydantic-settings 的 mode="before" validator 执行时默认值已填充，
        无法区分"用户设置"与"硬编码默认值"。改为在 __init__ 中提前注入：
        当 AQUEDUCT_ 前缀的环境变量未设置（os.environ 和 .env 中均无）时，
        从 _FALLBACK_VAR_MAP 中读取第三方变量名注入 os.environ。

        后续接入 Codex / DeepSeek / 千问等厂商时，只需在 _FALLBACK_VAR_MAP
        中追加映射即可。
        """
        self._inject_fallback_env_vars()
        super().__init__(**data)

    @classmethod
    def _inject_fallback_env_vars(cls) -> None:
        """将 _FALLBACK_VAR_MAP 中的第三方变量注入 os.environ。

        仅当 AQUEDUCT_ 前缀对应的字段未被设置时（os.environ 中无对应变量）才注入，
        不覆盖已有值。
        """
        for env_key, field_name in _FALLBACK_VAR_MAP.items():
            aqueduct_key = f"AQUEDUCT_{field_name.upper()}"
            if aqueduct_key not in os.environ and env_key in os.environ:
                os.environ[aqueduct_key] = os.environ[env_key]

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
    def _validate_config(self) -> Settings:
        """配置合法性校验。"""
        from ..exceptions import ConfigError

        # 校验日志级别
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if self.log_level.upper() not in valid_levels:
            raise ConfigError(
                f"无效的日志级别: {self.log_level!r}，可选值: {', '.join(sorted(valid_levels))}"
            )

        # 校验执行参数
        if self.execution_max_rows <= 0:
            raise ConfigError(f"execution_max_rows 必须为正整数，当前值: {self.execution_max_rows}")
        if self.execution_timeout_seconds <= 0:
            raise ConfigError(
                f"execution_timeout_seconds 必须为正数，当前值: {self.execution_timeout_seconds}"
            )

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
