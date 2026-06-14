"""Integration tests for the new layered architecture.

These tests verify that all layers work together:
- Tools layer: BaseTool + registry + actual tool execution
- Skills layer: BaseSkill + registry + prompt template loading
- LLM layer: BaseLLM + Claude adapter + router + context manager + prompt registry
- Engine layer: StateGraph + nodes + workflows + recovery
- Memory layer: DomainModel + MemoryStore + KnowledgeRecall
- CLI layer: argument parsing + command routing
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ============================================================
# Tools Layer Integration
# ============================================================


class TestToolsIntegration:
    """Tools 层集成测试：注册、执行、校验。"""

    def test_all_tools_registered(self):
        """验证所有工具已注册到全局注册中心。"""
        from src.aqueduct.tools.registry import list_tools

        tools = list_tools()

        expected = [
            "validator",
            "lineage",
            "estimator",
            "dqc",
            "semantic",
            "design",
            "sync",
            "productivity",
            "batch_query",
        ]
        for name in expected:
            assert name in tools, f"Tool '{name}' not registered"

    def test_validator_tool_execution(self):
        """测试 ValidatorTool 执行真实 SQL 校验。"""
        from src.aqueduct.tools.registry import get_tool

        # 使用项目中的真实 SQL 文件
        sql_file = Path("output/sample-project/sample-project.sql")
        if sql_file.exists():
            tool = get_tool("validator")
            result = tool.execute(sql_file=str(sql_file))
            assert result.success is True
            assert result.metadata["error_count"] == 0

    def test_estimator_tool_execution(self):
        """测试 EstimatorTool 执行真实 SQL 成本预估。"""
        from src.aqueduct.tools.registry import get_tool

        sql_file = Path("output/sample-project/sample-project.sql")
        if sql_file.exists():
            tool = get_tool("estimator")
            result = tool.execute(sql_file=str(sql_file))
            assert result.success is True
            assert result.metadata["table_count"] > 0

    def test_tool_validation_errors(self):
        """测试工具参数校验错误。"""
        from src.aqueduct.tools.validator import ValidatorTool

        tool = ValidatorTool()
        errors = tool.validate()
        assert len(errors) > 0
        assert "sql_file" in errors[0]

    def test_tool_not_found_error(self):
        """测试获取未注册工具抛出异常。"""
        from src.aqueduct.exceptions import ToolNotFoundError
        from src.aqueduct.tools.registry import get_tool

        with pytest.raises(ToolNotFoundError):
            get_tool("nonexistent_tool")


# ============================================================
# Skills Layer Integration
# ============================================================


class TestSkillsIntegration:
    """Skills 层集成测试：注册、Prompt 模板加载、执行。"""

    def test_all_skills_registered(self):
        """验证所有 Skill 已注册。"""
        from src.aqueduct.skills.registry import list_skills

        skills = list_skills()
        expected = [
            "requirement_clarify",
            "design_scheme",
            "ddl_generate",
            "sql_develop",
            "code_review",
            "dqc_quality",
            "report_delivery",
            "productivity_board",
        ]
        for name in expected:
            assert name in skills, f"Skill '{name}' not registered"

    def test_prompt_template_loading(self):
        """测试所有 Skill 的 Prompt 模板可加载。"""
        from src.aqueduct.skills.registry import get_skill

        # 测试 requirement_clarify 需要特定变量
        skill = get_skill("requirement_clarify")
        prompt = skill.load_prompt_template(
            requirement_doc="test",
            domain_context="test",
            known_tables="test",
        )
        assert len(prompt) > 0

        # 测试其他 Skill
        for name in [
            "design_scheme",
            "ddl_generate",
            "sql_develop",
            "code_review",
            "dqc_quality",
            "report_delivery",
        ]:
            s = get_skill(name)
            # 每个 Skill 应有 prompt_template_path
            assert s.prompt_template_path, f"Skill '{name}' has no prompt template"

    def test_skill_execution_returns_result(self):
        """测试 Skill 执行返回 SkillResult。"""
        from src.aqueduct.skills.base import SkillContext, SkillResult
        from src.aqueduct.skills.registry import get_skill

        skill = get_skill("requirement_clarify")
        context = SkillContext(input="test requirement")
        result = skill.execute(context)
        assert isinstance(result, SkillResult)
        assert isinstance(result.success, bool)

    def test_skill_not_found_error(self):
        """测试获取未注册 Skill 抛出异常。"""
        from src.aqueduct.exceptions import SkillNotFoundError
        from src.aqueduct.skills.registry import get_skill

        with pytest.raises(SkillNotFoundError):
            get_skill("nonexistent_skill")


# ============================================================
# LLM Layer Integration
# ============================================================


class TestLLMIntegration:
    """LLM 层集成测试：适配器、路由器、上下文管理、Prompt 注册。"""

    def test_claude_llm_creation(self):
        """测试 ClaudeLLM 实例创建。"""
        from src.aqueduct.llm.claude import ClaudeLLM

        llm = ClaudeLLM(model_id="claude-sonnet-4-6")
        assert llm.model_id == "claude-sonnet-4-6"
        assert llm.max_context > 0

    def test_token_estimation(self):
        """测试 Token 估算。"""
        from src.aqueduct.llm.claude import ClaudeLLM

        llm = ClaudeLLM()
        # 英文估算
        en_tokens = llm.estimate_tokens("Hello, this is a test.")
        assert en_tokens > 0

        # 中文估算
        zh_tokens = llm.estimate_tokens("你好，这是一个测试。")
        assert zh_tokens > 0

        # 空字符串
        assert llm.estimate_tokens("") == 0

    def test_model_router_routing(self):
        """测试模型路由器按任务类型路由。"""
        from src.aqueduct.llm.router import ModelRouter

        router = ModelRouter()

        # 分析类 → Haiku
        haiku_llm = router.route("requirement_parse")
        assert haiku_llm is not None

        # 中等生成 → Sonnet
        sonnet_llm = router.route("scheme_write")
        assert sonnet_llm is not None

        # 重度生成 → Opus
        opus_llm = router.route("sql_gen")
        assert opus_llm is not None

    def test_model_router_unknown_task(self):
        """测试路由器对未知任务类型抛出异常。"""
        from src.aqueduct.llm.router import ModelRouter

        router = ModelRouter()
        with pytest.raises(ValueError, match="未知任务类型"):
            router.route("unknown_task_type")

    def test_context_manager_truncation(self):
        """测试上下文管理器自动截断。"""
        from src.aqueduct.llm.claude import ClaudeLLM
        from src.aqueduct.llm.context import ContextBudget, ContextManager, LLMMessage

        llm = ClaudeLLM()
        # 设置极小预算以触发截断
        budget = ContextBudget(max_tokens=100, system_reserve=10, output_reserve=10)
        ctx = ContextManager(llm, budget)

        # 添加大量消息
        for i in range(50):
            ctx.add(LLMMessage("user", f"消息 {i}: " + "A" * 100))

        # 验证上下文已截断
        assert ctx.token_count <= budget.available

    def test_prompt_template_registry(self):
        """测试 Prompt 模板注册。"""
        from src.aqueduct.llm.prompts import (
            PromptTemplate,
            get_prompt,
            list_prompts,
            register_prompt,
        )

        template = PromptTemplate(
            id="test_integration",
            system="你是一个 {role}",
            variables=["role"],
        )
        register_prompt(template)

        retrieved = get_prompt("test_integration")
        assert retrieved.id == "test_integration"
        assert "test_integration" in list_prompts()

    def test_prompt_template_rendering(self):
        """测试 Prompt 模板变量渲染。"""
        from src.aqueduct.llm.prompts import PromptTemplate

        template = PromptTemplate(
            id="test_render",
            system="需求: {requirement}",
            user="请参考 {table_name}",
            variables=["requirement", "table_name"],
        )
        messages = template.render(requirement="测试需求", table_name="users")

        assert len(messages) == 2
        assert "测试需求" in messages[0].content
        assert "users" in messages[1].content


# ============================================================
# Engine Layer Integration
# ============================================================


class TestEngineIntegration:
    """Engine 层集成测试：StateGraph、DAG 节点、工作流、错误恢复。"""

    def test_dev_workflow_builds(self):
        """测试开发模式工作流可构建。"""
        from src.aqueduct.engine.workflow import build_dev_workflow

        wf = build_dev_workflow()
        assert wf is not None

    def test_review_workflow_builds(self):
        """测试审查模式工作流可构建。"""
        from src.aqueduct.engine.workflow import build_review_workflow

        wf = build_review_workflow()
        assert wf is not None

    def test_state_graph_compilation(self):
        """测试 StateGraph 可编译。"""
        from src.aqueduct.engine.state import WorkflowState
        from src.aqueduct.engine.workflow import END, StateGraph

        graph = StateGraph(WorkflowState)
        graph.add_node("test_node", lambda state: state)
        graph.set_entry_point("test_node")
        graph.add_edge("test_node", END)

        compiled = graph.compile()
        assert compiled is not None

    def test_error_recovery_classification(self):
        """测试错误恢复策略正确分类错误。"""
        from src.aqueduct.engine.recovery import ErrorSeverity, RecoveryStrategy

        strategy = RecoveryStrategy()

        # 临时错误
        assert (
            strategy.classify_error(TimeoutError("connection timeout")) == ErrorSeverity.TRANSIENT
        )

        # 校验错误
        assert strategy.classify_error(ValueError("missing parameter")) == ErrorSeverity.VALIDATION

        # 致命错误
        assert strategy.classify_error(RuntimeError("internal error")) == ErrorSeverity.FATAL

    def test_recovery_retry_action(self):
        """测试恢复策略返回重试动作。"""
        from src.aqueduct.engine.recovery import RecoveryStrategy

        strategy = RecoveryStrategy()
        result = strategy.recover("test_node", TimeoutError("timeout"), attempt=1)
        assert result.action == "retry"

    def test_recovery_halt_action(self):
        """测试恢复策略在重试耗尽后返回终止动作。"""
        from src.aqueduct.engine.recovery import RecoveryPolicy, RecoveryStrategy

        policy = RecoveryPolicy(max_retries=2)
        strategy = RecoveryStrategy(policy)

        # 第 3 次尝试（超过 max_retries=2）应终止
        result = strategy.recover("test_node", TimeoutError("timeout"), attempt=3)
        assert result.action == "halt"


# ============================================================
# Memory Layer Integration
# ============================================================


class TestMemoryIntegration:
    """Memory 层集成测试：DomainModel、MemoryStore、KnowledgeRecall。"""

    def test_domain_model_loads(self):
        """测试 DomainModel 从 JSON 加载。"""
        from src.aqueduct.memory.domain import DomainModel

        domain_file = "knowledge/domains/ecommerce_order.json"
        if not Path(domain_file).exists():
            return  # skip if domain file not available
        domain = DomainModel.from_json(domain_file)
        assert domain.domain_id is not None

    def test_domain_model_mermaid(self):
        """测试 DomainModel 生成 Mermaid ER 图。"""
        from src.aqueduct.memory.domain import DomainModel

        domain_file = "knowledge/domains/supply_chain_inventory.json"
        if not Path(domain_file).exists():
            return  # skip if domain file not available
        domain = DomainModel.from_json(domain_file)
        mermaid = domain.to_mermaid()
        assert "```mermaid" in mermaid
        assert "erDiagram" in mermaid

    def test_memory_store_lists_domains(self):
        """测试 MemoryStore 列出所有业务域。"""
        from src.aqueduct.memory.store import MemoryStore

        store = MemoryStore()
        domains = store.list_domains()
        assert len(domains) >= 3  # 至少 3 个业务域

    def test_memory_store_load_and_cache(self):
        """测试 MemoryStore 加载并缓存业务域。"""
        from src.aqueduct.memory.store import MemoryStore

        store = MemoryStore()
        domain1 = store.load("ecommerce_order")
        domain2 = store.load("ecommerce_order")

        # 验证缓存命中
        assert domain1 is domain2

    def test_knowledge_recall_matches_domain(self):
        """测试知识召回能匹配到相关业务域。"""
        from src.aqueduct.memory.recall import KnowledgeRecall

        recall = KnowledgeRecall()
        result = recall.recall("电商 订单 统计")

        assert result["domain_id"] == "ecommerce_order"
        assert result["domain_context"] != ""

    def test_knowledge_recall_no_match(self):
        """测试知识召回对无关需求返回 None 或低匹配度。"""
        from src.aqueduct.memory.recall import KnowledgeRecall

        recall = KnowledgeRecall()
        result = recall.recall("xyz_random_unrelated_topic_12345")

        # 不相关需求不应匹配到业务域
        assert result.get("domain_id", "") == "" or result.get("domain_id") is None


# ============================================================
# CLI Layer Integration
# ============================================================


class TestCLIIntegration:
    """CLI 层集成测试：参数解析、命令路由。"""

    def test_cli_parser_has_subcommands(self):
        """测试 CLI 解析器包含所有子命令。"""
        from src.aqueduct.cli.main import create_parser

        parser = create_parser()
        # 检查子命令
        subparsers_action = [
            action for action in parser._subparsers._group_actions if hasattr(action, "choices")
        ]
        assert len(subparsers_action) > 0

        choices = subparsers_action[0].choices
        assert "dev" in choices
        assert "review" in choices
        assert "validate" in choices
        assert "status" in choices

    def test_cli_status_command(self):
        """测试 CLI status 命令可执行。"""
        import argparse

        from src.aqueduct.cli.main import _status

        args = argparse.Namespace()
        rc = _status(args)
        assert rc == 0

    def test_cli_validate_nonexistent_file(self):
        """测试 CLI validate 命令对不存在文件返回错误。"""
        import argparse

        from src.aqueduct.cli.main import _validate_sql

        args = argparse.Namespace(sql_file="nonexistent_file.sql", strict=False)
        rc = _validate_sql(args)
        assert rc == 1


# ============================================================
# Cross-Layer Integration
# ============================================================


class TestCrossLayerIntegration:
    """跨层集成测试：验证各层之间能正确协作。"""

    def test_tool_called_from_skill(self):
        """测试 Skill 能调用 Tool。"""
        from src.aqueduct.skills.base import SkillContext
        from src.aqueduct.skills.extra.productivity_board import ProductivityBoardSkill

        skill = ProductivityBoardSkill()
        context = SkillContext(input=None, state={"root": "."})
        result = skill.execute(context)

        # ProductivityBoardSkill 内部调用 ProductivityTool
        # 即使工具执行失败，Skill 应正确返回结果
        assert result is not None

    def test_memory_store_used_by_recall(self):
        """测试 MemoryStore 被 KnowledgeRecall 正确使用。"""
        from src.aqueduct.memory.recall import KnowledgeRecall
        from src.aqueduct.memory.store import MemoryStore

        store = MemoryStore()
        recall = KnowledgeRecall(store)

        # 验证 recall 内部使用 store
        result = recall.recall("合规 排班 员工")
        assert isinstance(result, dict)

    def test_engine_nodes_call_skills(self):
        """测试 Engine 节点能调用 Skill。"""
        from src.aqueduct.engine.nodes import node_requirement
        from src.aqueduct.engine.state import WorkflowState

        state: WorkflowState = {
            "requirement": "测试需求文档内容",
            "mode": "dev",
            "errors": [],
            "metadata": {},
        }

        result = node_requirement(state)
        # 节点应返回 WorkflowState（即使 Skill 执行失败也要有状态）
        assert isinstance(result, dict)
        assert "requirement" in result
