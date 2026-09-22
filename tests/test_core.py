"""_run_pipeline 和 _run_fix_loop 单元测试。

覆盖核心管道执行器的关键行为，避免触碰 LLM。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.aqueduct.core import _run_fix_loop, _run_pipeline
from src.aqueduct.exceptions import LLMEmptyResponseError, WorkflowHaltError

# ============================================================
# _run_pipeline 测试
# ============================================================


class TestRunPipeline:
    """_run_pipeline 行为测试。"""

    @staticmethod
    def _make_state(name: str = "test_req") -> dict:
        return {
            "requirement": "test requirement",
            "mode": "dev",
            "metadata": {"requirement_name": name},
            "errors": [],
            "artifacts": [],
        }

    @staticmethod
    def _make_phases(*names: str) -> list[tuple[str, MagicMock]]:
        """创建一组 mock 节点函数，每个返回 state 不变。"""
        phases = []
        for name in names:
            fn = MagicMock(side_effect=lambda s: s, name=f"node_{name}")
            phases.append((name, fn))
        return phases

    def test_pipeline_runs_all_phases(self):
        """所有阶段按顺序执行。"""
        state = self._make_state()
        phases = self._make_phases("req", "design", "sql", "review")

        result = _run_pipeline(state, phases)

        assert result.success
        assert not result.halted
        for _name, fn in phases:
            fn.assert_called_once()

    def test_pipeline_stops_on_halt_error(self):
        """节点抛 WorkflowHaltError 时管道终止。"""
        state = self._make_state()
        phases = self._make_phases("req", "design", "sql", "review")
        # 第二个节点抛终止错误
        phases[1][1].side_effect = WorkflowHaltError("需求不明确")

        result = _run_pipeline(state, phases)

        assert not result.success
        assert result.halted
        # 第一个节点执行了，第二个没有（因为抛异常终止）
        phases[0][1].assert_called_once()
        # 后续节点未执行
        phases[2][1].assert_not_called()

    def test_pipeline_collects_errors(self):
        """非致命异常记录到 errors 但不终止管道。"""
        state = self._make_state()
        phases = self._make_phases("req", "design", "sql")
        phases[1][1].side_effect = ValueError("some error")

        _run_pipeline(state, phases)

        assert len(state["errors"]) == 1
        assert "some error" in state["errors"][0]
        # 第三个节点仍然执行了
        phases[2][1].assert_called_once()

    def test_pipeline_progress_callback(self):
        """on_progress 回调按序触发。"""
        state = self._make_state()
        phases = self._make_phases("a", "b", "c")
        progress_calls: list[str] = []

        def on_progress(name: str, idx: int, total: int, _state) -> None:
            progress_calls.append(name)

        _run_pipeline(state, phases, on_progress=on_progress)

        assert progress_calls == ["a", "b", "c"]

    def test_pipeline_interactive_confirm_continue(self):
        """interactive=True 且 confirm 返回 True 时继续执行。"""
        state = self._make_state()
        phases = self._make_phases("req", "sql")

        _run_pipeline(
            state,
            phases,
            interactive=True,
            confirm_after="req",
            on_confirm=lambda _s: True,
        )

        phases[0][1].assert_called_once()
        phases[1][1].assert_called_once()

    def test_pipeline_interactive_confirm_stop(self):
        """interactive=True 且 confirm 返回 False 时停止。"""
        state = self._make_state()
        phases = self._make_phases("req", "sql")

        result = _run_pipeline(
            state,
            phases,
            interactive=True,
            confirm_after="req",
            on_confirm=lambda _s: False,
        )

        phases[0][1].assert_called_once()
        # 第二个节点未执行
        phases[1][1].assert_not_called()
        assert result.halted

    def test_pipeline_halt_error_in_errors_list(self):
        """errors 列表中的消息包含终止标记时也终止管道。"""
        state = self._make_state()
        phases = self._make_phases("req", "design")

        def failing_node(s):
            s.setdefault("errors", []).append("[终止] 致命错误")
            return s

        phases[0] = ("req", failing_node)

        result = _run_pipeline(state, phases)

        assert result.halted
        # 第二个节点未执行
        phases[1][1].assert_not_called()


# ============================================================
# _run_fix_loop 测试
# ============================================================


class TestRunFixLoop:
    """_run_fix_loop 修复循环测试。"""

    @staticmethod
    def _make_state_with_issues() -> dict:
        return {
            "requirement": "test",
            "mode": "dev",
            "metadata": {"requirement_name": "test_req"},
            "errors": [],
            "artifacts": [],
            "sql_content": "SELECT 1 FROM dual",
            "_review_issues": [
                {"severity": "Error", "message": "missing WHERE clause"},
            ],
            "_needs_fix_loop": True,
            "fix_iterations": 0,
        }

    @patch(
        "src.aqueduct.engine.nodes.helpers.save_artifact", side_effect=lambda s, n, c: f"output/{n}"
    )
    @patch("src.aqueduct.engine.nodes.helpers.is_valid_sql", return_value=True)
    @patch("src.aqueduct.engine.nodes.helpers.extract_sql_block", side_effect=lambda x: x)
    @patch(
        "src.aqueduct.engine.nodes.helpers.call_llm", return_value="SELECT 1 FROM dual WHERE 1=1"
    )
    def test_fix_loop_fixes_sql(self, _mock_llm, _mock_extract, _mock_valid, _mock_save):
        """LLM 返回有效修复 SQL 时，state 被更新。"""
        state = self._make_state_with_issues()

        with patch("src.aqueduct.config.settings.get_settings") as mock_settings:
            mock_settings.return_value.max_fix_iterations = 2
            result = _run_fix_loop(state)

        assert result["sql_content"] == "SELECT 1 FROM dual WHERE 1=1"
        assert result["fix_iterations"] == 1
        assert result.get("_needs_fix_loop") is False

    @patch(
        "src.aqueduct.engine.nodes.helpers.save_artifact", side_effect=lambda s, n, c: f"output/{n}"
    )
    @patch("src.aqueduct.engine.nodes.helpers.is_valid_sql", return_value=True)
    @patch("src.aqueduct.engine.nodes.helpers.extract_sql_block", side_effect=lambda x: x)
    @patch(
        "src.aqueduct.engine.nodes.helpers.call_llm", return_value="SELECT 1 FROM dual WHERE 1=1"
    )
    def test_fix_loop_rewrites_canonical_sql_file(
        self, _mock_llm, _mock_extract, _mock_valid, _mock_save, tmp_path
    ):
        """修复 SQL 回写规范文件 Phase4-{req}.sql，_fixN 仅作审计副本。

        回归来源：2026-09-09 perf4-check eval——只落 _fix1.sql 不回写规范文件，
        交付物本体停留在修复前（CASE WHEN 形态），evals 对全部 Phase4-*.sql
        做 linter，按规范文件判 FAIL。
        """
        canonical = tmp_path / "Phase4-test_req.sql"
        canonical.write_text("SELECT 1 FROM dual", encoding="utf-8")

        state = self._make_state_with_issues()
        state["sql_file"] = str(canonical)

        with patch("src.aqueduct.config.settings.get_settings") as mock_settings:
            mock_settings.return_value.max_fix_iterations = 2
            result = _run_fix_loop(state)

        assert canonical.read_text(encoding="utf-8") == "SELECT 1 FROM dual WHERE 1=1"
        # state 指向规范文件而非 _fixN 审计副本
        assert Path(result["sql_file"]).name == "Phase4-test_req.sql"

    @patch(
        "src.aqueduct.engine.nodes.helpers.save_artifact", side_effect=lambda s, n, c: f"output/{n}"
    )
    @patch("src.aqueduct.engine.nodes.helpers.is_valid_sql", return_value=True)
    @patch("src.aqueduct.engine.nodes.helpers.extract_sql_block", side_effect=lambda x: x)
    @patch(
        "src.aqueduct.engine.nodes.helpers.call_llm", return_value="SELECT 1 FROM dual WHERE 1=1"
    )
    def test_fix_loop_max_iterations(self, _mock_llm, _mock_extract, _mock_valid, _mock_save):
        """达到最大迭代次数时不再修复。"""
        state = self._make_state_with_issues()
        state["fix_iterations"] = 2

        with patch("src.aqueduct.config.settings.get_settings") as mock_settings:
            mock_settings.return_value.max_fix_iterations = 2
            result = _run_fix_loop(state)

        # 没有修改 SQL
        assert result["sql_content"] == "SELECT 1 FROM dual"

    @patch(
        "src.aqueduct.engine.nodes.helpers.save_artifact", side_effect=lambda s, n, c: f"output/{n}"
    )
    @patch("src.aqueduct.engine.nodes.helpers.is_valid_sql", return_value=False)
    @patch("src.aqueduct.engine.nodes.helpers.extract_sql_block", side_effect=lambda x: x)
    @patch("src.aqueduct.engine.nodes.helpers.call_llm", return_value="not valid sql at all")
    def test_fix_loop_invalid_llm_output(self, _mock_llm, _mock_extract, _mock_valid, _mock_save):
        """LLM 返回无效 SQL 时保留原 SQL。"""
        state = self._make_state_with_issues()
        original_sql = state["sql_content"]

        with patch("src.aqueduct.config.settings.get_settings") as mock_settings:
            mock_settings.return_value.max_fix_iterations = 2
            result = _run_fix_loop(state)

        assert result["sql_content"] == original_sql

    @patch(
        "src.aqueduct.engine.nodes.helpers.save_artifact", side_effect=lambda s, n, c: f"output/{n}"
    )
    @patch("src.aqueduct.engine.nodes.helpers.is_valid_sql", return_value=False)
    @patch("src.aqueduct.engine.nodes.helpers.extract_sql_block", side_effect=lambda x: x)
    @patch("src.aqueduct.engine.nodes.helpers.call_llm", return_value="not valid sql at all")
    def test_fix_loop_invalid_output_clears_flag(
        self, _mock_llm, _mock_extract, _mock_valid, _mock_save
    ):
        """回归测试: LLM 修复输出无效时 _needs_fix_loop 应被设为 False，防止无限循环。"""
        state = self._make_state_with_issues()
        state["_needs_fix_loop"] = True

        with patch("src.aqueduct.config.settings.get_settings") as mock_settings:
            mock_settings.return_value.max_fix_iterations = 2
            result = _run_fix_loop(state)

        assert result["_needs_fix_loop"] is False

    @patch("src.aqueduct.engine.nodes.helpers.call_llm")
    def test_fix_loop_llm_failure_degrades_gracefully(self, mock_llm):
        """回归测试: 修复调用重试耗尽不应炸管道——降级保留原 SQL 继续后续阶段。

        v4 计时复测（2026-09-01）实测：sql_fix 空响应重试耗尽后
        LLMEmptyResponseError 从 _run_fix_loop 一路上抛杀死整条管道，
        Phase 6 报告全部丢失。正确行为是降级：记录错误、保留未修复 SQL、
        清除回环标志、管道继续。
        """
        mock_llm.side_effect = LLMEmptyResponseError(
            "LLM 返回空响应（已重试 2 次）: task_type=sql_fix, model=glm-5.3"
        )
        state = self._make_state_with_issues()

        with patch("src.aqueduct.config.settings.get_settings") as mock_settings:
            mock_settings.return_value.max_fix_iterations = 2
            result = _run_fix_loop(state)  # 不应抛出异常

        assert result["sql_content"] == "SELECT 1 FROM dual"
        assert result["_needs_fix_loop"] is False
        assert any("修复循环" in err for err in result["errors"])


class TestFixFeedbackHardening:
    """刀② 修复反馈保真度（run 5 复盘，2026-09-18）。

    run 5 实录：修复环 2 轮从 7 → 10 个 Critical（越修越多振荡），
    `hour_slot  0`（缺比较运算符）字面语法错误存活 2 轮修复。
    契约：
    - 修复 prompt 问题条目携带维度归属（确定性条目带 [规范]/[试跑] 原文）
    - 修复输出本地 re-lint：ERROR 数回退（越修越多）→ 拒绝本次修复保留原 SQL
      （与无效输出同路径：清回环标志，防 fix_iterations 不增的死循环）
    - 有净改善（含部分修复）→ 接受，交回审查复检
    """

    _SQL_1_ERROR = "select * from dwd.dwd_order_detail_di where inc_day = '20260101';"
    _SQL_2_ERRORS = (
        "select * from dwd.dwd_order_detail_di where inc_day = '20260101';\n"
        "select total / cnt as ratio from dwd.dwd_order_detail_di where inc_day = '20260101';"
    )

    @staticmethod
    def _make_state(sql: str, issues: list[dict]) -> dict:
        return {
            "requirement": "test",
            "mode": "dev",
            "metadata": {"requirement_name": "test_req"},
            "errors": [],
            "artifacts": [],
            "sql_content": sql,
            "_review_issues": issues,
            "_needs_fix_loop": True,
            "fix_iterations": 0,
        }

    @patch(
        "src.aqueduct.engine.nodes.helpers.save_artifact", side_effect=lambda s, n, c: f"output/{n}"
    )
    @patch("src.aqueduct.engine.nodes.helpers.is_valid_sql", return_value=True)
    @patch("src.aqueduct.engine.nodes.helpers.extract_sql_block", side_effect=lambda x: x)
    @patch("src.aqueduct.engine.nodes.helpers.call_llm")
    def test_issue_lines_carry_dimension(self, mock_llm, _mock_extract, _mock_valid, _mock_save):
        """修复 prompt 的问题条目带维度归属（定位上下文，反馈保真）。"""
        captured: list[str] = []
        mock_llm.side_effect = lambda st, t, p: captured.append(p) or "select 1"

        state = self._make_state(
            self._SQL_1_ERROR,
            [
                {
                    "severity": "Critical",
                    "message": "除法未判零 (line 42)",
                    "dimension": "逻辑正确性",
                }
            ],
        )
        with patch("src.aqueduct.config.settings.get_settings") as mock_settings:
            mock_settings.return_value.max_fix_iterations = 2
            _run_fix_loop(state)

        assert captured, "修复 prompt 已发送"
        assert "[Critical][逻辑正确性] 除法未判零 (line 42)" in captured[0]

    @patch(
        "src.aqueduct.engine.nodes.helpers.save_artifact", side_effect=lambda s, n, c: f"output/{n}"
    )
    @patch("src.aqueduct.engine.nodes.helpers.is_valid_sql", return_value=True)
    @patch("src.aqueduct.engine.nodes.helpers.extract_sql_block", side_effect=lambda x: x)
    @patch("src.aqueduct.engine.nodes.helpers.call_llm")
    def test_reject_fix_when_lint_regresses(self, mock_llm, _mock_extract, _mock_valid, _mock_save):
        """re-lint ERROR 数回退（1→2）→ 拒绝修复保留原 SQL，不清算迭代（防振荡）。"""
        mock_llm.side_effect = lambda st, t, p: self._SQL_2_ERRORS  # 比 before 多 1 ERROR

        state = self._make_state(
            self._SQL_1_ERROR,
            [{"severity": "Critical", "message": "SELECT * 违规 (line 1)", "dimension": "规范"}],
        )
        with patch("src.aqueduct.config.settings.get_settings") as mock_settings:
            mock_settings.return_value.max_fix_iterations = 2
            result = _run_fix_loop(state)

        assert result["sql_content"] == self._SQL_1_ERROR, "回退修复被拒绝，保留原 SQL"
        assert result["_needs_fix_loop"] is False, "拒绝即终止回环（迭代不增，清标志防死循环）"
        assert result["fix_iterations"] == 0

    @patch(
        "src.aqueduct.engine.nodes.helpers.save_artifact", side_effect=lambda s, n, c: f"output/{n}"
    )
    @patch("src.aqueduct.engine.nodes.helpers.is_valid_sql", return_value=True)
    @patch("src.aqueduct.engine.nodes.helpers.extract_sql_block", side_effect=lambda x: x)
    @patch("src.aqueduct.engine.nodes.helpers.call_llm")
    def test_accept_fix_with_partial_progress(
        self, mock_llm, _mock_extract, _mock_valid, _mock_save
    ):
        """ERROR 数净改善（2→1，部分修复）→ 接受，交回审查复检。"""
        fixed = (
            "select order_id, total / nullif(cnt, 0) as ratio from dwd.dwd_order_detail_di "
            "where inc_day = '20260101';\n"
            "select * from dwd.dwd_order_detail_di where inc_day = '20260101';"
        )
        mock_llm.side_effect = lambda st, t, p: fixed

        state = self._make_state(
            self._SQL_2_ERRORS,
            [
                {"severity": "Critical", "message": "SELECT * 违规 (line 1)", "dimension": "规范"},
                {"severity": "Critical", "message": "除法未判零 (line 2)", "dimension": "规范"},
            ],
        )
        with patch("src.aqueduct.config.settings.get_settings") as mock_settings:
            mock_settings.return_value.max_fix_iterations = 2
            result = _run_fix_loop(state)

        assert result["sql_content"] == fixed, "部分修复被接受"
        assert result["fix_iterations"] == 1

    def test_tpl_has_deterministic_feedback_contract(self):
        """sql_fix 模板含确定性校验反馈契约（按行号定位 + 语法完整性自检）。"""
        from src.aqueduct.config.settings import get_settings

        tpl = (get_settings().prompt_dir / "sql_fix.tpl.md").read_text(encoding="utf-8")
        assert "确定性校验反馈" in tpl
        assert "按行号" in tpl, "确定性条目必须按行号定位修复"
        assert "语法" in tpl, "必须自检修复行的语法完整性（run 5: 缺比较运算符存活 2 轮）"

    def test_design_ddl_tpl_has_scope_constraint(self):
        """design_ddl 模板（Phase 1 B 直出/Phase 2 合并）含产出范围硬约束。"""
        from src.aqueduct.config.settings import get_settings

        tpl = (get_settings().prompt_dir / "design_ddl.tpl.md").read_text(encoding="utf-8")
        assert "产出表" in tpl, "必须要求 DDL 覆盖需求/设计方案的产出表清单"
        assert "逐表" in tpl, "必须逐表建齐，不允许汇总式带过"
        assert "禁止只建" in tpl, "必须显式禁止只建 TMP 中间层（run 6: 两张 ADS 表全缺）"

    def test_ddl_generate_tpl_has_scope_constraint(self):
        """ddl_generate 模板（Phase 3 回退）含产出范围硬约束。"""
        from src.aqueduct.config.settings import get_settings

        tpl = (get_settings().prompt_dir / "ddl_generate.tpl.md").read_text(encoding="utf-8")
        assert "产出表" in tpl, "必须要求 DDL 覆盖需求/设计方案的产出表清单"
        assert "逐表" in tpl, "必须逐表建齐，不允许汇总式带过"
        assert "禁止只建" in tpl, "必须显式禁止只建 TMP 中间层（run 6: 两张 ADS 表全缺）"

    @patch("src.aqueduct.core._run_fix_loop")
    def test_pipeline_no_infinite_fix_loop(self, mock_fix):
        """回归测试: 审查反复发现 Critical 时，达到 max_fix_iterations 后应继续后续阶段。"""

        # 每次修复循环递增 fix_iterations
        def increment_fix(s):
            s["fix_iterations"] = s.get("fix_iterations", 0) + 1
            return s

        mock_fix.side_effect = increment_fix
        state = {
            "requirement": "test",
            "mode": "dev",
            "metadata": {"requirement_name": "test_req"},
            "errors": [],
            "artifacts": [],
            "sql_content": "SELECT 1",
            "fix_iterations": 0,
        }

        sql_fn = MagicMock(side_effect=lambda s: s, name="node_sql")
        review_fn = MagicMock(name="node_review")

        # 审查每次都设置 _needs_fix_loop = True
        def review_always_finds_issues(s):
            s["_needs_fix_loop"] = True
            s["_review_issues"] = [{"severity": "Critical", "message": "some issue"}]
            return s

        review_fn.side_effect = review_always_finds_issues
        phases = [
            ("sql", sql_fn),
            ("review", review_fn),
            ("dqc", MagicMock(side_effect=lambda s: s)),
        ]

        # 平台能力隔离：本测试 mock 了 get_settings，adapter 单例若未缓存
        # （单文件跑/全量跑顺序差）loader 会读到 mocked platform → ConfigError。
        # 历史绿跑靠全量套件里先前用例缓存单例侥幸通过（顺序依赖）。
        with (
            patch("src.aqueduct.config.settings.get_settings") as mock_settings,
            patch("src.aqueduct.platform.get_platform_adapter") as mock_adapter,
        ):
            mock_settings.return_value.max_fix_iterations = 2
            mock_adapter.return_value.has_capability.return_value = False
            _run_pipeline(state, phases)

        # SQL 节点不应被无限调用（最多 3 次：初始 + 2 次修复回环）
        assert sql_fn.call_count <= 3
        # DQC 节点应该被执行（管道没有被卡在修复循环中）
        phases[2][1].assert_called_once()


class TestNullifDialectConflict:
    """第五刀 5a：nullif 方言冲突（run 7 实录，2026-09-22）。

    试跑报 Invalid function nullif ×25——平台 Hive 不支持 nullif，但
    linter 报错文案、审查维度 prompt、sql_develop 模板三处都在处方
    `nullif(divisor, 0)`，LLM 照抄带病上试跑。修复方向：全部改处方
    CASE WHEN 等价形式（linter 逻辑本就接受 case 守护，无需改动）。
    """

    def test_sql_develop_tpl_prescribes_case_when_not_nullif(self):
        """sql_develop 模板（Phase 4 生成源头）不得处方 nullif。"""
        from src.aqueduct.config.settings import get_settings

        tpl = (get_settings().prompt_dir / "sql_develop.tpl.md").read_text(encoding="utf-8")
        assert "nullif" not in tpl.lower(), "模板处方 nullif 是 25 处带病 SQL 的源头"
        assert "case when" in tpl.lower(), "除法保护必须改处方 CASE WHEN 等价形式"

    def test_review_dimension_focus_prescribes_case_when_not_nullif(self):
        """审查维度 prompt（规范与影响）不得处方 NULLIF。"""
        from src.aqueduct.engine.nodes.review import _REVIEW_DIMENSIONS

        standards = next(d for d in _REVIEW_DIMENSIONS if d["key"] == "standards")
        focus = standards["focus"]
        assert "nullif" not in focus.lower(), "审查 prompt 处方 NULLIF 会随审查意见进修复环"
        assert "case when" in focus.lower(), "必须引导审查认可 CASE WHEN 等价形式"

    def test_sql_fix_tpl_has_platform_dialect_constraint(self):
        """sql_fix 模板含平台方言约束：试跑报 Invalid function 时改写等价形式。"""
        from src.aqueduct.config.settings import get_settings

        tpl = (get_settings().prompt_dir / "sql_fix.tpl.md").read_text(encoding="utf-8")
        assert "Invalid function" in tpl, "必须点名试跑报错形态（run 7: Invalid function nullif）"
        assert "case when" in tpl.lower(), "必须给出改写方向：等价 CASE WHEN 形式"


# ============================================================
# OPT-4: _split_design_and_ddl 测试
# ============================================================


class TestSplitDesignAndDdl:
    """合并调用的响应拆分测试。"""

    def test_split_normal_response(self):
        """正常响应：设计方案 + SQL 代码块。"""
        from src.aqueduct.engine.nodes.design import _split_design_and_ddl

        response = """## 取数逻辑
从 source_table 取数

## 字段映射
| 目标字段 | 源字段 | 转换逻辑 |
|----------|--------|----------|

```sql
CREATE TABLE test (id INT COMMENT '主键')
```"""
        design, ddl = _split_design_and_ddl(response)

        assert "取数逻辑" in design
        assert "字段映射" in design
        assert "CREATE TABLE" in ddl
        assert "```sql" not in ddl
        assert "```" not in ddl

    def test_split_no_sql_block(self):
        """无 SQL 代码块时，全部作为设计方案。"""
        from src.aqueduct.engine.nodes.design import _split_design_and_ddl

        response = "## 设计方案\n一些设计内容，没有 SQL 代码块。"
        design, ddl = _split_design_and_ddl(response)

        assert design == "## 设计方案\n一些设计内容，没有 SQL 代码块。"
        assert ddl == ""

    def test_split_sql_before_design(self):
        """SQL 在文本最前面时，设计方案为空。"""
        from src.aqueduct.engine.nodes.design import _split_design_and_ddl

        response = """```sql
CREATE TABLE test (id INT)
```"""
        design, ddl = _split_design_and_ddl(response)

        assert design == ""
        assert "CREATE TABLE" in ddl


# ============================================================
# OPT-4: node_ddl 跳过测试
# ============================================================


class TestNodeDdlSkip:
    """Phase 3 DDL 节点跳过行为测试。"""

    def test_ddl_skips_when_already_produced(self):
        """DDL 已在 Phase 2 合并调用中产出时，Phase 3 跳过 LLM 调用。"""
        from src.aqueduct.engine.nodes.ddl import node_ddl

        state = {
            "requirement": "test",
            "mode": "dev",
            "metadata": {"requirement_name": "test_req"},
            "errors": [],
            "artifacts": [],
            "design_scheme": "some design",
            "ddl_content": "CREATE TABLE test (id INT)",
            "ddl_file": "output/Phase3-表结构.sql",
        }

        result = node_ddl(state)

        # DDL 内容未变
        assert result["ddl_content"] == "CREATE TABLE test (id INT)"
        assert result["ddl_file"] == "output/Phase3-表结构.sql"
        assert result["metadata"]["ddl_done"] == "true"

    def test_ddl_fallback_when_not_produced(self):
        """DDL 未在合并调用中产出时，Phase 3 独立生成（通过 mock LLM）。"""
        from src.aqueduct.engine.nodes.ddl import node_ddl

        state = {
            "requirement": "test",
            "mode": "dev",
            "metadata": {"requirement_name": "test_req"},
            "errors": [],
            "artifacts": [],
            "design_scheme": "some design",
            "domain_context": "",
        }

        with (
            patch(
                "src.aqueduct.engine.nodes.ddl.call_llm", return_value="CREATE TABLE test (id INT)"
            ),
            patch(
                "src.aqueduct.engine.nodes.ddl.save_artifact",
                side_effect=lambda s, n, c: f"output/{n}",
            ),
        ):
            result = node_ddl(state)

        assert result["ddl_content"] == "CREATE TABLE test (id INT)"
        assert result["metadata"]["ddl_done"] == "true"


# ============================================================
# review_mode 统一到 _run_pipeline（workflow.py 退役）
# ============================================================


class TestReviewPipeline:
    """审查模式接线契约：CLI 不再绕过 core 走 StateGraph。"""

    def test_review_phases_defined(self):
        """_REVIEW_PHASES 与旧 build_review_workflow 的节点序列一致。"""
        from src.aqueduct.core import _REVIEW_PHASES

        assert [name for name, _ in _REVIEW_PHASES] == [
            "requirement",
            "review",
            "dqc",
            "report",
        ]

    def test_review_mode_builds_state_and_runs_pipeline(self, tmp_path):
        """review_mode 读两份 SQL 构建 state，走 _run_pipeline(_REVIEW_PHASES)。"""
        from src.aqueduct.core import _REVIEW_PHASES, Aqueduct, AqueductResult

        online = tmp_path / "online.sql"
        changed = tmp_path / "changed.sql"
        online.write_text("ONLINE SQL", encoding="utf-8")
        changed.write_text("CHANGED SQL", encoding="utf-8")

        sentinel = AqueductResult({"errors": [], "artifacts": []})
        with patch("src.aqueduct.core._run_pipeline", return_value=sentinel) as mock_run:
            result = Aqueduct().review_mode(str(online), str(changed), desc="口径变更")

        assert result is sentinel
        state, phases = mock_run.call_args[0][0], mock_run.call_args[0][1]
        assert phases is _REVIEW_PHASES
        assert state["mode"] == "review"
        assert state["online_sql"] == "ONLINE SQL"
        assert state["changed_sql"] == "CHANGED SQL"
        assert state["requirement"] == "口径变更"
        assert state["errors"] == []
        assert state["artifacts"] == []
        assert state["metadata"]["requirement_name"] == "online"

    def test_review_mode_accepts_output_dir_and_progress(self, tmp_path):
        """output_dir / on_progress 透传，与 change() 同构。"""
        from src.aqueduct.core import Aqueduct, AqueductResult

        online = tmp_path / "a.sql"
        changed = tmp_path / "b.sql"
        online.write_text("A", encoding="utf-8")
        changed.write_text("B", encoding="utf-8")

        sentinel = AqueductResult({"errors": [], "artifacts": []})
        progress = MagicMock()
        with (
            patch("src.aqueduct.core._run_pipeline", return_value=sentinel) as mock_run,
            patch("pathlib.Path.cwd", return_value=tmp_path),
        ):
            Aqueduct().review_mode(
                str(online), str(changed), output_dir=str(tmp_path), on_progress=progress
            )

        assert mock_run.call_args[1]["on_progress"] is progress
        assert mock_run.call_args[0][0]["metadata"]["output_dir"] == str(tmp_path)

    def test_cli_review_routes_to_core(self, tmp_path):
        """CLI 审查模式调用 Aqueduct.review_mode，不再 import workflow。"""
        import argparse

        from src.aqueduct.cli.main import _review_mode
        from src.aqueduct.core import AqueductResult

        online = tmp_path / "online.sql"
        changed = tmp_path / "changed.sql"
        online.write_text("A", encoding="utf-8")
        changed.write_text("B", encoding="utf-8")
        args = argparse.Namespace(online_sql=str(online), changed_sql=str(changed), desc="")

        sentinel = AqueductResult({"errors": [], "artifacts": []})
        with (
            patch("src.aqueduct.cli.main.Aqueduct") as mock_cls,
            patch("src.aqueduct.cli.main.build_review_workflow", create=True) as mock_legacy,
        ):
            mock_cls.return_value.review_mode.return_value = sentinel
            rc = _review_mode(args)

        mock_legacy.assert_not_called()
        assert rc == 0
        mock_cls.return_value.review_mode.assert_called_once()

    def test_cli_review_reports_errors_with_rc1(self, tmp_path):
        """review_mode 返回带 errors 的结果时 CLI 打 WARN 并返回 1。"""
        import argparse

        from src.aqueduct.cli.main import _review_mode
        from src.aqueduct.core import AqueductResult

        online = tmp_path / "online.sql"
        changed = tmp_path / "changed.sql"
        online.write_text("A", encoding="utf-8")
        changed.write_text("B", encoding="utf-8")
        args = argparse.Namespace(online_sql=str(online), changed_sql=str(changed), desc="")

        sentinel = AqueductResult({"errors": ["boom"], "artifacts": []})
        with (
            patch("src.aqueduct.cli.main.Aqueduct") as mock_cls,
            patch("src.aqueduct.cli.main.build_review_workflow", create=True) as mock_legacy,
        ):
            mock_cls.return_value.review_mode.return_value = sentinel
            rc = _review_mode(args)

        mock_legacy.assert_not_called()
        assert rc == 1
