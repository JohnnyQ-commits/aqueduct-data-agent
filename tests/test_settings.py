"""配置管理测试。"""

from __future__ import annotations

from src.aqueduct.config.settings import Settings, get_settings


class TestSettings:
    """配置测试。"""

    def test_default_settings_creation(self):
        settings = Settings()
        assert settings.log_level == "INFO"
        assert settings.max_workflow_steps == 20
        assert settings.sql_max_file_size_kb == 512

    def test_project_root_is_absolute(self):
        settings = Settings()
        assert settings.project_root.is_absolute()

    def test_paths_resolved_to_absolute(self):
        settings = Settings()
        assert settings.knowledge_dir.is_absolute()
        assert settings.prompt_dir.is_absolute()
        assert settings.output_dir.is_absolute()

    def test_knowledge_dir_points_to_correct_location(self):
        settings = Settings()
        assert settings.knowledge_dir.name == "domains"
        assert "knowledge" in str(settings.knowledge_dir)

    def test_prompt_dir_points_to_skills_prompt(self):
        settings = Settings()
        assert settings.prompt_dir.name == "prompt"
        assert "skills" in str(settings.prompt_dir)

    def test_allowed_sql_extensions(self):
        settings = Settings()
        assert ".sql" in settings.allowed_sql_extensions

    def test_cache_works(self):
        get_settings.cache_clear()
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2
        get_settings.cache_clear()


class TestSettingsValidation:
    """配置校验测试。"""

    def test_empty_model_ids_allowed(self):
        """模型 ID 为空不报错，只发出警告。"""
        settings = Settings()
        assert settings.default_analysis_model == ""

    def test_workflow_timeout_default(self):
        settings = Settings()
        assert settings.workflow_timeout_seconds == 3600


class TestExecutionSettings:
    """执行能力配置测试。"""

    def test_execution_enabled_default(self):
        """execution_enabled 默认为 True。"""
        from src.aqueduct.config.settings import Settings

        settings = Settings()
        assert settings.execution_enabled is True

    def test_execution_timeout_default(self):
        """execution_timeout_seconds 默认 300。"""
        from src.aqueduct.config.settings import Settings

        settings = Settings()
        assert settings.execution_timeout_seconds == 300

    def test_execution_max_rows_default(self):
        """execution_max_rows 默认 1000。"""
        from src.aqueduct.config.settings import Settings

        settings = Settings()
        assert settings.execution_max_rows == 1000
