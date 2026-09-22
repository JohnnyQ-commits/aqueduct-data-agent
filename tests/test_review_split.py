"""PERF-11 sql_review 维度拆分并行 + 审查解析契约修复测试。

隐性缺陷实录（2026-09-09 四个 eval 报告交叉验证）：审查报告全部含高质量
Critical（GMV 口径失真、目标表名冲突、string 字典序比较），但可解析格式
（`[Critical]` 行）匹配数全为 0——code_review.tpl.md 教模型输出表格，
_parse_review_issues 只认方括号列表行，LLM 审查发现从未进入修复循环
（历次绿跑 "issues=0" 是解析盲区，修复循环只被 linter/试跑注入触发过）。

契约（本批修复 + 拆分）：
- 输出格式锁定列表行：`- [Critical] / - [Warning] / - [Confirm] 描述`
- 严重级别三分：Critical=代码层可修复缺陷（触发修复循环）、Warning=质量
  问题、Confirm=需人工确认的口径/依赖问题——greenfield 待确认是常态，
  不得因"需确认"触发修复循环空转或 halt
- 单块审查拆 3 维度并行（P0-3 DQC 拆分范式）：同输入不同审查透镜，
  每维一次小调用，固定顺序合并；单维失败重试一次后降级（banner+errors），
  全维失败走既有审查失败降级路径
- 分块路径（>100 行多语句）保持全量模板不拆
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.aqueduct.engine.nodes.review import (
    _REVIEW_DIMENSIONS,
    _parse_review_issues,
    _review_by_dimensions,
    node_review,
)
from src.aqueduct.skills.base import SkillContext
from src.aqueduct.skills.code_review import CodeReviewDimensionSkill

_DESIGN = "## 取数逻辑\n订单主表按城市聚合，zz_design_marker 仅统计有效订单"
_DDL = "CREATE TABLE zz_ddl_marker (id bigint, city string) PARTITIONED BY (inc_day string)"
# 短于 50 字符：node 层测试绕开 _lint_sql_issues 的真实 Validator
_SQL = "select city, count(1) from t where inc_day='d'"

_DIM_NAMES = [d["name"] for d in _REVIEW_DIMENSIONS]


def _make_state() -> dict:
    """Phase 4.5 时点的最小 state（Phase 2/3/4 产物均已就位）。"""
    return {
        "requirement_summary": "统计每日各城市订单量",
        "sql_content": _SQL,
        "domain_context": "",
        "validation_result": {"issues": []},
        "design_scheme": _DESIGN,
        # 7b 起 DDL 门禁会对「无 insert 的产出表」报 Critical——默认置空，
        # 需要 DDL 的测试（对齐/prompt 标记）各自显式设置
        "ddl_content": "",
        "metadata": {"requirement_name": "test"},
        "errors": [],
        "artifacts": [],
    }


def _dim_response(name: str, issues: list[str]) -> str:
    """构造合法的单维度响应（含解析契约要求的审查结论标记行）。"""
    counts = {"Critical": 0, "Warning": 0, "Confirm": 0}
    lines = [f"### {name} — 分析", "", "本维度核对完毕。", "", f"### {name} — 发现的问题", ""]
    for line in issues:
        sev = line.split("]")[0].lstrip("- [")
        counts[sev] = counts.get(sev, 0) + 1
        lines.append(line)
    if not issues:
        lines.append("- 无")
    lines += [
        "",
        f"**审查结论**: Critical: {counts['Critical']}, "
        f"Warning: {counts['Warning']}, Confirm: {counts['Confirm']}",
    ]
    return "\n".join(lines)


# ── 维度定义 ─────────────────────────────────────────────────────────────────


class TestReviewDimensions:
    """_REVIEW_DIMENSIONS：拆分单元的定义契约。"""

    def test_three_dimensions_unique_keys(self):
        """3 个维度、key 唯一、顺序稳定（合并顺序即此顺序）。"""
        assert [d["key"] for d in _REVIEW_DIMENSIONS] == ["alignment", "logic", "standards"]
        assert len({d["name"] for d in _REVIEW_DIMENSIONS}) == 3

    def test_focus_nonempty_no_cross_dimension_name(self):
        """每个维度 focus 非空，且不含其他维度的完整名（prompt 识别依赖）。"""
        for dim in _REVIEW_DIMENSIONS:
            assert dim.get("focus")
            for other in _REVIEW_DIMENSIONS:
                if other["key"] != dim["key"]:
                    assert other["name"] not in dim["focus"], (
                        f"{dim['key']} 的 focus 不得包含 {other['name']}（prompt 维度识别会串）"
                    )


# ── 解析契约 ─────────────────────────────────────────────────────────────────


class TestParseContract:
    """_parse_review_issues：[Confirm] 级别纳入解析。"""

    def test_confirm_line_parsed(self):
        issues = _parse_review_issues("- [Confirm] 源表无实付金额字段，gmv 口径需业务确认")
        assert issues == [
            {
                "severity": "Confirm",
                "message": "源表无实付金额字段，gmv 口径需业务确认",
                "dimension": "",
            }
        ]

    def test_mixed_severities_parsed(self):
        text = (
            "- [Critical] 除法未判零 (line 42)\n"
            "- [Warning] 子查询嵌套 3 层\n"
            "- [Confirm] 状态码字典未知\n"
        )
        issues = _parse_review_issues(text)
        assert [i["severity"] for i in issues] == ["Critical", "Warning", "Confirm"]

    def test_dedupe_across_dimension_sections(self):
        """同一问题被两个维度发现 → 合并报告里只解析一次。"""
        text = (
            "## 维度审查: A\n- [Critical] 缺分区过滤\n\n## 维度审查: B\n- [Critical] 缺分区过滤\n"
        )
        assert len(_parse_review_issues(text)) == 1

    def test_issues_carry_dimension_from_section_header(self):
        """记分卡按维度细分（P2）：每条发现归属最近的「## 维度审查:」章节。"""
        text = (
            "## 维度审查: 需求与设计对齐\n"
            "- [Critical] 需求指标 rose 未在 SQL 中体现\n\n"
            "## 维度审查: 逻辑正确性\n"
            "- [Warning] JOIN 扇出放大 sum 指标\n"
        )
        issues = _parse_review_issues(text)
        assert [(i["message"], i["dimension"]) for i in issues] == [
            ("需求指标 rose 未在 SQL 中体现", "需求与设计对齐"),
            ("JOIN 扇出放大 sum 指标", "逻辑正确性"),
        ]

    def test_issues_without_section_header_get_empty_dimension(self):
        """单块审查（维度拆分降级回退）无章节头 → dimension 为空串（记分卡渲染「综合」）。"""
        issues = _parse_review_issues("- [Warning] 子查询嵌套 3 层")
        assert issues[0]["dimension"] == ""


class TestDeterministicIssueDimensions:
    """lint/试跑发现是确定性透镜，归属固定维度（不经 LLM 章节推断）。"""

    def test_lint_issues_tagged_standards(self):
        from src.aqueduct.engine.nodes.review import _lint_sql_issues

        issues = _lint_sql_issues(
            "select * from dwd.dwd_order_detail_di where inc_day = '20260101';"
        )
        assert issues, "select * 应触发 linter Critical"
        assert all(i["dimension"] == "规范" for i in issues)

    def test_trial_run_issues_tagged_trial(self):
        from src.aqueduct.engine.nodes.review import _trial_run_issues

        state = _make_state() | {"sql_content": _SQL + " group by city having count(1) > 0"}
        with (
            patch("src.aqueduct.platform.get_platform_adapter") as mock_adapter,
            patch("src.aqueduct.tools.registry.get_tool") as mock_tool,
            patch(
                "src.aqueduct.engine.nodes.sql._run_trial_selects",
                return_value={"errors": ["表不存在: dm_demo.foo"], "passed": 0, "tested": 1},
            ),
        ):
            mock_adapter.return_value.has_capability.return_value = True
            mock_tool.return_value.execute.return_value.success = True
            issues = _trial_run_issues(state)
        assert issues, "试跑失败应注入 Critical"
        assert all(i["dimension"] == "试跑" for i in issues)

    def test_trial_gate_timeout_is_confirm_not_critical(self):
        """第六刀 6b：试跑超时是慢查询标注（Confirm），不是 SQL 缺陷（Critical）。

        run 8 实录：两轮修复环被同 2 条超时 Critical 卡死终止，而终版 SQL
        门禁复跑 3/3 通过——大表 count 全分区扫描真实耗时在 5min 阈值边缘
        抖动，超时改判 Confirm 不触发修复环（语法错/字段错仍 Critical）。
        """
        from src.aqueduct.engine.nodes.review import _trial_run_issues

        state = _make_state() | {"sql_content": _SQL + " group by city having count(1) > 0"}
        with (
            patch("src.aqueduct.platform.get_platform_adapter") as mock_adapter,
            patch("src.aqueduct.tools.registry.get_tool") as mock_tool,
            patch(
                "src.aqueduct.engine.nodes.sql._run_trial_selects",
                return_value={
                    "errors": [],
                    "timeouts": ["SELECT #1: 任务超时 (5 min)"],
                    "passed": 1,
                    "tested": 1,
                },
            ),
        ):
            mock_adapter.return_value.has_capability.return_value = True
            mock_tool.return_value.execute.return_value.success = True
            issues = _trial_run_issues(state)
        assert issues, "超时须落盘待确认标注，不得静默"
        assert all(i["severity"] == "Confirm" for i in issues), "超时不得触发修复环"
        assert all(i["dimension"] == "试跑" for i in issues)
        assert all("超时" in i["message"] for i in issues)


class TestTrialSelectsTimeoutClassification:
    """第六刀 6b：_run_trial_selects 超时错误单列（errors 不含超时）。

    超时 = 平台已受理并执行（语法/字段无错），只是慢——与"表不存在/
    字段不对齐"类硬失败分桶，门禁据此降级 Confirm。
    """

    def test_timeout_error_goes_to_timeouts_bucket(self):
        from src.aqueduct.engine.nodes.sql import _run_trial_selects

        with (
            patch("src.aqueduct.platform.get_platform_adapter") as mock_adapter,
            patch("src.aqueduct.tools.registry.get_tool") as mock_get_tool,
        ):
            mock_adapter.return_value.has_capability.return_value = True
            exec_result = MagicMock()
            exec_result.success = False
            exec_result.error = "任务超时 (5 min)"
            mock_get_tool.return_value.execute.return_value = exec_result
            trial = _run_trial_selects("select city, count(1) from t group by city;")
        assert trial["errors"] == [], "超时不是 SQL 硬失败"
        assert trial["timeouts"] and "SELECT #1" in trial["timeouts"][0]
        assert trial["timeouts"][0].endswith("任务超时 (5 min)")

    def test_hard_error_stays_in_errors_bucket(self):
        from src.aqueduct.engine.nodes.sql import _run_trial_selects

        with (
            patch("src.aqueduct.platform.get_platform_adapter") as mock_adapter,
            patch("src.aqueduct.tools.registry.get_tool") as mock_get_tool,
        ):
            mock_adapter.return_value.has_capability.return_value = True
            exec_result = MagicMock()
            exec_result.success = False
            exec_result.error = "编译失败: 列 `sign_time` 不存在"
            mock_get_tool.return_value.execute.return_value = exec_result
            trial = _run_trial_selects("select sign_time from t;")
        assert len(trial["errors"]) == 1, "字段错等硬失败仍进 errors（Critical）"
        assert trial["timeouts"] == []

    def test_timeout_counts_as_passed_for_scoring(self):
        """超时计入通过数（SQL 语义有效，仅慢）——记分卡真实试跑不因平台负载误判。"""
        from src.aqueduct.engine.nodes.sql import _run_trial_selects

        with (
            patch("src.aqueduct.platform.get_platform_adapter") as mock_adapter,
            patch("src.aqueduct.tools.registry.get_tool") as mock_get_tool,
        ):
            mock_adapter.return_value.has_capability.return_value = True
            exec_result = MagicMock()
            exec_result.success = False
            exec_result.error = "任务超时 (5 min)"
            mock_get_tool.return_value.execute.return_value = exec_result
            trial = _run_trial_selects("select 1;")
        assert trial["passed"] == trial["tested"], "超时语句计入通过（慢查询标注另行列出）"


# ── 维度 prompt（skill 层） ──────────────────────────────────────────────────


class TestDimensionPrompt:
    """CodeReviewDimensionSkill：维度名/focus 注入 + 上下文解析。"""

    def test_prompt_contains_focus_and_context(self):
        skill = CodeReviewDimensionSkill()
        context = SkillContext(
            input={
                "requirement_desc": "统计每日各城市订单量",
                "sql_content": _SQL,
                "design_scheme": _DESIGN,
                "ddl_content": _DDL,
                "validation_result": {"issues": []},
                "dimension": {
                    "key": "alignment",
                    "name": "需求与设计对齐",
                    "focus": "zz_focus_marker 逐项核对需求覆盖",
                },
            },
            state={},
        )
        result = skill.execute(context)
        assert result.success
        prompt = result.data["prompt"]
        assert "需求与设计对齐" in prompt
        assert "zz_focus_marker" in prompt
        assert "zz_design_marker" in prompt
        assert "zz_ddl_marker" in prompt

    def test_state_fallback_and_placeholder(self):
        """input 缺失从 state 兜底；两处都没有渲染占位，不泄漏 $变量。"""
        skill = CodeReviewDimensionSkill()
        context = SkillContext(
            input={
                "requirement_desc": "统计每日各城市订单量",
                "sql_content": _SQL,
                "dimension": {
                    "key": "logic",
                    "name": "逻辑正确性",
                    "focus": "zz_focus_marker",
                },
            },
            state={"design_scheme": _DESIGN},
        )
        result = skill.execute(context)
        assert result.success
        prompt = result.data["prompt"]
        assert "zz_design_marker" in prompt, "design_scheme 应从 state 兜底"
        assert prompt.count("未获取") >= 1, "缺失的 ddl_content 渲染占位"
        assert "$ddl_content" not in prompt
        assert "$dimension_name" not in prompt
        assert "$dimension_focus" not in prompt


# ── 拆分执行（并行 + 降级） ──────────────────────────────────────────────────


class TestReviewByDimensions:
    """_review_by_dimensions：3 维并行、固定顺序合并、失败降级。"""

    @staticmethod
    def _call_router(queues: dict[str, list[str]]):
        """按 prompt 中的维度名路由到各自的响应队列（识别即拆分的核心）。"""

        def fake_call_llm(state, task_type, prompt):
            for dim in _REVIEW_DIMENSIONS:
                if dim["name"] in prompt:
                    return queues[dim["key"]].pop(0)
            raise AssertionError(f"prompt 未识别维度: {prompt[:120]}")

        return fake_call_llm

    def test_merges_in_dimension_order(self):
        state = _make_state()
        queues = {
            d["key"]: [_dim_response(d["name"], ["- [Warning] w1"])] for d in _REVIEW_DIMENSIONS
        }
        with patch(
            "src.aqueduct.engine.nodes.review.call_llm",
            side_effect=self._call_router(queues),
        ):
            merged = _review_by_dimensions(state, _SQL, "test")

        positions = [merged.find(f"## 维度审查: {n}") for n in _DIM_NAMES]
        assert all(p >= 0 for p in positions), "每个维度一个章节"
        assert positions == sorted(positions), "章节顺序 = _REVIEW_DIMENSIONS 顺序"

    def test_invalid_response_retried_once(self):
        """无审查结论标记的响应（罐头错误）→ 重试一次，有效即接受。"""
        state = _make_state()
        queues = {d["key"]: [_dim_response(d["name"], [])] for d in _REVIEW_DIMENSIONS}
        queues["logic"] = ["网关拥塞 55 字符罐头错误", _dim_response("逻辑正确性", [])]
        calls: list[str] = []

        def fake_call_llm(st, task_type, prompt):
            calls.append(prompt)
            return self._call_router(queues)(st, task_type, prompt)

        with patch("src.aqueduct.engine.nodes.review.call_llm", side_effect=fake_call_llm):
            merged = _review_by_dimensions(state, _SQL, "test")

        assert len(calls) == 4, "3 维度 + 1 次重试"
        assert "审查降级" not in merged
        assert "逻辑正确性 — 分析" in merged

    def test_single_dimension_degrades(self):
        """单维两次无效 → 降级 banner + errors 记录，其余维度正常。"""
        state = _make_state()
        queues = {d["key"]: [_dim_response(d["name"], [])] for d in _REVIEW_DIMENSIONS}
        queues["standards"] = ["罐头错误A", "罐头错误B"]
        with patch(
            "src.aqueduct.engine.nodes.review.call_llm",
            side_effect=self._call_router(queues),
        ):
            merged = _review_by_dimensions(state, _SQL, "test")

        assert "[审查降级]" in merged
        assert "规范与影响" in merged, "降级章节仍保留维度名"
        assert "需求与设计对齐 — 分析" in merged, "未失败维度不受影响"
        assert any("规范与影响" in e for e in state["errors"]), "降级记 errors"

    def test_all_dimensions_fail_returns_none(self):
        """全部维度失败 → None（调用方走既有审查失败降级路径）。"""
        state = _make_state()
        queues = {d["key"]: ["罐头", "罐头"] for d in _REVIEW_DIMENSIONS}
        with patch(
            "src.aqueduct.engine.nodes.review.call_llm",
            side_effect=self._call_router(queues),
        ):
            assert _review_by_dimensions(state, _SQL, "test") is None


# ── node 接线 ────────────────────────────────────────────────────────────────


class TestNodeReviewWiring:
    """node_review：单块路径走拆分、Confirm 路由、分块路径不拆。"""

    def test_single_block_uses_dimension_split(self):
        """单语句 SQL → 3 次维度调用（每维一次），报告落盘。"""
        state = _make_state()
        queues = {d["key"]: [_dim_response(d["name"], [])] for d in _REVIEW_DIMENSIONS}
        saved: list[str] = []

        def fake_call_llm(st, task_type, prompt):
            assert task_type == "sql_review"
            return TestReviewByDimensions._call_router(queues)(st, task_type, prompt)

        with (
            patch("src.aqueduct.engine.nodes.review.call_llm", side_effect=fake_call_llm),
            patch(
                "src.aqueduct.engine.nodes.review.save_artifact",
                side_effect=lambda st, name, content: saved.append(name) or "output/x.md",
            ),
            patch("src.aqueduct.engine.nodes.review.start_dqc_speculative"),
            patch("src.aqueduct.engine.nodes.review.start_knowledge_speculative"),
        ):
            node_review(state)

        assert saved == ["Phase5-test_审查报告.md"]
        assert state["_needs_fix_loop"] is False
        assert state.get("review_confirmations") == []

    def test_confirm_routed_not_fix_loop(self):
        """[Confirm] 进 review_confirmations，不进 _review_issues、不触发修复循环。"""
        state = _make_state()
        queues = {
            "alignment": [
                _dim_response("需求与设计对齐", ["- [Confirm] 源表无实付金额字段，口径需业务确认"])
            ],
            "logic": [_dim_response("逻辑正确性", [])],
            "standards": [_dim_response("规范与影响", ["- [Confirm] 状态码字典未知"])],
        }
        with (
            patch(
                "src.aqueduct.engine.nodes.review.call_llm",
                side_effect=TestReviewByDimensions._call_router(queues),
            ),
            patch("src.aqueduct.engine.nodes.review.save_artifact", return_value="output/x.md"),
            patch("src.aqueduct.engine.nodes.review.start_dqc_speculative"),
            patch("src.aqueduct.engine.nodes.review.start_knowledge_speculative"),
        ):
            node_review(state)

        assert state["_needs_fix_loop"] is False, "Confirm 不得触发修复循环"
        assert len(state["review_confirmations"]) == 2
        assert state.get("_review_issues") is None or state["_review_issues"] == []

    def test_critical_triggers_fix_loop(self):
        """[Critical]（代码可修复缺陷）照常触发修复循环，进 _review_issues。"""
        state = _make_state()
        queues = {
            "alignment": [_dim_response("需求与设计对齐", [])],
            "logic": [
                _dim_response("逻辑正确性", ["- [Critical] 除法未判零 (line 42) — 加 nullif"])
            ],
            "standards": [_dim_response("规范与影响", [])],
        }
        with (
            patch(
                "src.aqueduct.engine.nodes.review.call_llm",
                side_effect=TestReviewByDimensions._call_router(queues),
            ),
            patch("src.aqueduct.engine.nodes.review.save_artifact", return_value="output/x.md"),
            patch("src.aqueduct.engine.nodes.review.start_dqc_speculative"),
            patch("src.aqueduct.engine.nodes.review.start_knowledge_speculative"),
        ):
            node_review(state)

        assert state["_needs_fix_loop"] is True
        criticals = [i for i in state["_review_issues"] if i["severity"] == "Critical"]
        assert criticals == [
            {
                "severity": "Critical",
                "message": "除法未判零 (line 42) — 加 nullif",
                "dimension": "逻辑正确性",
            }
        ]
        assert state["review_confirmations"] == []

    def test_warning_only_no_false_max_iterations_log(self, caplog):
        """仅 Warning 时不得打"已达最大修复次数"假消息（perf11-check2 实录：
        Critical 走 halt 分支到不了该消息，历史 warning_count 恒 0 掩盖，解析
        契约修复后 warning>0 首次触发——消息对 fix_iterations=0 是谎言）。"""
        state = _make_state()
        queues = {
            d["key"]: [_dim_response(d["name"], ["- [Warning] w1"])] for d in _REVIEW_DIMENSIONS
        }
        with (
            patch(
                "src.aqueduct.engine.nodes.review.call_llm",
                side_effect=TestReviewByDimensions._call_router(queues),
            ),
            patch("src.aqueduct.engine.nodes.review.save_artifact", return_value="output/x.md"),
            patch("src.aqueduct.engine.nodes.review.start_dqc_speculative"),
            patch("src.aqueduct.engine.nodes.review.start_knowledge_speculative"),
            caplog.at_level("INFO", logger="src.aqueduct.engine.nodes.review"),
        ):
            node_review(state)

        assert state["_needs_fix_loop"] is False
        logged = [r.getMessage() for r in caplog.records]
        assert any("跳过修复循环" in m for m in logged), "应有 Warning-only 跳过消息"
        assert not any("已达最大修复次数" in m for m in logged), "不得打假的最大轮数消息"

    def test_chunk_path_not_split(self):
        """>100 行多语句 → 分块路径（全量模板），不进维度拆分。"""
        state = _make_state()
        # 两条顶层语句各 60 行 → 121 行、2 块 → _should_parallel_review
        stmt = "\n".join(f"-- 注释行 {i}" for i in range(59)) + "\nselect 1;"
        state["sql_content"] = stmt + "\n" + stmt
        prompts: list[str] = []

        def fake_call_llm(st, task_type, prompt):
            prompts.append(prompt)
            return "审查结果"

        with (
            patch("src.aqueduct.engine.nodes.review.call_llm", side_effect=fake_call_llm),
            patch("src.aqueduct.engine.nodes.review.save_artifact", return_value="output/x.md"),
            patch("src.aqueduct.engine.nodes.review.start_dqc_speculative"),
            patch("src.aqueduct.engine.nodes.review.start_knowledge_speculative"),
            # 本机有真实登录态时长 SQL 会触发真实平台试跑——钉死离线
            patch("src.aqueduct.platform.get_platform_adapter") as mock_adapter,
        ):
            mock_adapter.return_value.has_capability.return_value = False
            node_review(state)

        assert len(prompts) == 2, "2 块 = 2 次调用（非 3 维度拆分）"
        assert "[审查第 1/2 个 SQL 语句块，对应完整脚本第 1 行起" in prompts[0]


# ── 刀① 分块伪影治理（2026-09-18 run 5 复盘） ────────────────────────────────


class TestChunkBoundaryArtifacts:
    """分块审查的切分伪影：块边界截断误判 + 行号错位（run 5 残留 Critical
    约 1/4 为假阳性）。

    审查报告原话（逐字）：「块末尾 drop table if exists tmp_detail 语句不完整，
    缺少分号或后续子句……若完整文件中语句连续则可降级为切分伪影」；
    「校验器疑似对整个脚本而非本块执行了校验，行号完全无法对应」。

    契约：
    - 拆分消费掉的结尾分号在 prompt 中补回（块以完整语句呈现，模型不再
      把「无分号」判成「语句不完整」）
    - 校验结果按块重算（块内行号），整脚本校验结果不再进分块 prompt
    - prompt 头标注本块对应完整脚本的起始行（LLM 发现可与整脚本坐标互查）
    """

    def test_chunk_prompt_appends_missing_semicolon(self):
        """拆分消费掉结尾分号的块，prompt 中以完整语句呈现。"""
        from src.aqueduct.engine.nodes.review import _build_chunk_prompt

        state = _make_state()
        prompt = _build_chunk_prompt(state, "select a from t1", 1, 2)
        assert "select a from t1\n;" in prompt, "补回的分号应独占一行（防落在行尾注释内）"

    def test_chunk_prompt_no_double_semicolon(self):
        """块本身以分号结尾（如文件末块）→ 不得出现双分号。"""
        from src.aqueduct.engine.nodes.review import _build_chunk_prompt

        state = _make_state()
        prompt = _build_chunk_prompt(state, "select 1;", 2, 2)
        assert "select 1;;" not in prompt

    def test_chunk_prompt_validation_scoped_to_block(self):
        """校验结果按块重算：块内违规以块内行号呈现，整脚本发现不再进块 prompt。"""
        from src.aqueduct.engine.nodes.review import _build_chunk_prompt

        state = _make_state()
        state["validation_result"] = {
            "filename": "etl.sql",
            "error_count": 1,
            "warn_count": 0,
            "issues": [
                {
                    "level": "ERROR",
                    "message": "整脚本第 300 行的违规 zz_whole_script_marker",
                    "line": 300,
                }
            ],
        }
        block = "-- 块内首行触发 select * 违规\nselect * from dwd.dwd_order_detail_di where inc_day='20260101'"
        prompt = _build_chunk_prompt(state, block, 1, 2)
        assert "SELECT *" in prompt.upper() or "select *" in prompt, "块内违规应在本块 prompt"
        assert "zz_whole_script_marker" not in prompt, "整脚本校验发现不得进分块 prompt"

    def test_chunk_header_announces_global_start_line(self):
        """prompt 头标注本块对应完整脚本的起始行，校验结果行号口径同步声明。"""
        from src.aqueduct.engine.nodes.review import _build_chunk_prompt

        state = _make_state()
        prompt = _build_chunk_prompt(state, "select a from t1", 2, 3, start_line=120)
        assert "第 120 行" in prompt
        assert "块内行号" in prompt, "行号口径必须显式声明，否则模型仍按整脚本坐标猜"

    def test_split_blocks_with_offsets(self):
        """分块附带起始行号（1 基），供 prompt 头标注全局坐标。"""
        from src.aqueduct.engine.nodes.review import _split_sql_blocks_with_offsets

        sql = "-- header\nselect a from t1;\nselect b\nfrom t2;"
        blocks = _split_sql_blocks_with_offsets(sql)
        assert [b for b, _ in blocks] == ["-- header\nselect a from t1", "select b\nfrom t2"]
        assert [line for _, line in blocks] == [1, 3]

    def test_parallel_review_prompts_are_block_scoped(self):
        """接线：并行审查的每块 prompt 带分号补全 + 全局起始行标注。"""
        state = _make_state()
        # 块 1（59 行，末行 select a from t1;）接块 2（60 行起）
        stmt1 = "\n".join(f"-- 注释行 {i}" for i in range(58)) + "\nselect a from t1"
        stmt2 = "\n".join(f"-- 尾块注释 {i}" for i in range(58)) + "\nselect b from t2;"
        state["sql_content"] = stmt1 + ";\n" + stmt2
        prompts: list[str] = []

        def fake_call_llm(st, task_type, prompt):
            prompts.append(prompt)
            return "审查结果"

        with (
            patch("src.aqueduct.engine.nodes.review.call_llm", side_effect=fake_call_llm),
            patch("src.aqueduct.engine.nodes.review.save_artifact", return_value="output/x.md"),
            patch("src.aqueduct.engine.nodes.review.start_dqc_speculative"),
            patch("src.aqueduct.engine.nodes.review.start_knowledge_speculative"),
            # 本机有真实登录态时长 SQL 会触发真实平台试跑——钉死离线
            patch("src.aqueduct.platform.get_platform_adapter") as mock_adapter,
        ):
            mock_adapter.return_value.has_capability.return_value = False
            node_review(state)

        assert len(prompts) == 2
        assert "select a from t1\n;" in prompts[0], "块 1 末尾补分号"
        assert "对应完整脚本第 1 行" in prompts[0], "块 1 起始行=1"
        assert "对应完整脚本第 60 行" in prompts[1], "块 2 起始行=语句行(含分号)+1"


class TestDdlColumnAlignment:
    """第七刀 7c：最终 INSERT select 与目标表 DDL 的确定性列对齐门禁。

    run 9 实录：唯一存活 Critical 是 LLM 审查发现（最终 select 8 列 vs
    DDL 11 列，缺 3 列 + 顺序错位），修复环 2 轮没修掉——列数/列序
    比对是纯机械活，LLM 审查不如确定性检查可靠。门禁在 review 层注入
    Critical（带 DDL 字段清单，修复环有构造性锚点），与 linter/试跑同构。
    """

    _DDL = """
    create table if not exists tmp_dw_demo.tmp_knight_base (
        emp_code string comment '员工编码',
        emp_name string comment '姓名',
        dept_code string comment '部门编码'
    );
    create table if not exists ads_dw_demo.ads_knight_weekly_di (
        emp_code string comment '员工编码',
        emp_name string comment '姓名',
        dept_code string comment '部门编码',
        position_name string comment '岗位',
        hire_date string comment '入职日期',
        is_new_emp tinyint comment '是否新员工'
    ) partitioned by (week_partition string);
    """

    @staticmethod
    def _insert(tbl: str, cols: list[str]) -> str:
        joiner = ",\n    "
        return (
            f"insert overwrite table {tbl} partition (week_partition = '2026-W24')\n"
            f"select\n    {joiner.join(cols)}\n"
            "from tmp_dw_demo.tmp_knight_base\nwhere dept_code is not null;"
        )

    def _issues(self, sql: str, ddl: str | None = None) -> list[dict[str, str]]:
        from src.aqueduct.engine.nodes.review import _ddl_column_alignment_issues

        state = _make_state() | {"sql_content": sql}
        if ddl is not None:
            state["ddl_content"] = ddl
        return _ddl_column_alignment_issues(state)

    def test_column_count_mismatch_is_critical_with_ddl_column_list(self):
        cols = ["emp_code", "concat(emp_name, 'x') as emp_name"]
        issues = self._issues(self._insert("ads_dw_demo.ads_knight_weekly_di", cols), self._DDL)
        assert len(issues) == 1, "8列 vs 6列类缺陷应报 1 条 Critical"
        assert issues[0]["severity"] == "Critical"
        assert issues[0]["dimension"] == "对齐"
        msg = issues[0]["message"]
        assert "2" in msg and "6" in msg, "消息含 select 列数与 DDL 列数"
        assert "position_name" in msg, "消息内嵌 DDL 字段清单（修复环构造性锚点）"
        assert "insert overwrite 按位置映射" in msg, "点名后果（错位写入）"

    def test_aligned_insert_passes(self):
        cols = [
            "emp_code",
            "emp_name",
            "dept_code",
            "position_name",
            "hire_date",
            "is_new_emp",
        ]
        assert self._issues(self._insert("ads_dw_demo.ads_knight_weekly_di", cols), self._DDL) == []

    def test_partition_column_not_counted_in_select(self):
        """分区字段在 partition (...) 子句，不进 select 列数比对。"""
        cols = [
            "emp_code",
            "emp_name",
            "dept_code",
            "position_name",
            "hire_date",
            "is_new_emp",
        ]
        sql = self._insert("ads_dw_demo.ads_knight_weekly_di", cols).replace(
            "partition (week_partition = '2026-W24')\n", ""
        )
        assert self._issues(sql, self._DDL) == []

    def test_expression_parens_do_not_inflate_count(self):
        """case when / 函数括号内的逗号不是列分隔符。"""
        cols = [
            "emp_code",
            "emp_name",
            "case when dept_code = 'D01' then 1 else 0 end as is_core_dept",
            "coalesce(position_name, '未知') as position_name",
            "hire_date",
            "is_new_emp",
        ]
        assert self._issues(self._insert("ads_dw_demo.ads_knight_weekly_di", cols), self._DDL) == []

    def test_insert_target_not_in_ddl_skipped(self):
        """目标表不在 DDL（tmp CTAS 等）→ 不做列数比对（零误报原则）。

        7b 后未知目标不再整单静默：未插入的 DDL 产出表由 coverage 检查
        兜底报 Critical，但列数比对只对 DDL 在册目标生效。
        """
        cols = ["a", "b"]
        issues = self._issues(self._insert("tmp_dw_demo.tmp_other", cols), self._DDL)
        assert all("列数" not in i["message"] for i in issues), "未知目标表不做列数比对"

    def test_no_ddl_content_skipped(self):
        cols = ["a", "b"]
        # _make_state 默认带 zz_ddl_marker 夹具 DDL，须显式清空才走「DDL 缺失跳过」分支
        assert self._issues(self._insert("ads_dw_demo.ads_knight_weekly_di", cols), "") == []

    def test_inline_comment_between_columns_ignored(self):
        cols = [
            "emp_code, -- 员工编码",
            "emp_name",
            "dept_code",
            "position_name",
            "hire_date",
            "is_new_emp",
        ]
        assert self._issues(self._insert("ads_dw_demo.ads_knight_weekly_di", cols), self._DDL) == []

    def test_node_review_injects_alignment_critical(self):
        """接线：对齐 Critical 进审查 issues 触发修复循环（与 linter 同路）。"""
        from src.aqueduct.engine.nodes.review import node_review

        state = _make_state() | {
            "sql_content": self._insert(
                "ads_dw_demo.ads_knight_weekly_di", ["emp_code", "emp_name"]
            ),
            "ddl_content": self._DDL,
        }
        with (
            patch("src.aqueduct.engine.nodes.review.call_llm", return_value="审查结论: 无问题"),
            patch("src.aqueduct.engine.nodes.review.save_artifact", return_value="output/x.md"),
            patch("src.aqueduct.engine.nodes.review.start_dqc_speculative"),
            patch("src.aqueduct.engine.nodes.review.start_knowledge_speculative"),
            patch("src.aqueduct.platform.get_platform_adapter") as mock_adapter,
        ):
            mock_adapter.return_value.has_capability.return_value = False
            node_review(state)
        dims = {i["dimension"] for i in state["_review_issues"]}
        assert "对齐" in dims, "对齐发现应进 _review_issues 触发修复循环"


class TestDdlDeliveryCoverage:
    """第七刀 7b（修正定性）：DDL 产出表缺 insert overwrite = 交付缺表。

    run 9 实录：manifest keyword fail（day 表名 / hl_scene_type 缺失）
    最初定性为「keyword 检查脆弱」，核对产物后推翻——最终 SQL 只有
    小时表一条 INSERT，day 表（SQL-1）整个缺失，keyword 检查抓的是真
    缺陷。确定性门禁延伸：DDL 非临时表没有对应 insert overwrite 目标 →
    Critical（tmp_ 前缀表由 CTAS 落地，不在其列）。
    """

    _DDL = """
    create table if not exists ads_dw_demo.ads_knight_weekly_di (
        emp_code string comment '员工编码',
        dept_code string comment '部门编码'
    ) partitioned by (inc_day string);
    create table if not exists ads_dw_demo.ads_knight_weekly_hour_di (
        emp_code string comment '员工编码',
        hour_bucket string comment '小时桶'
    ) partitioned by (inc_day string);
    """

    def _issues(self, sql: str, ddl: str = _DDL) -> list[dict[str, str]]:
        from src.aqueduct.engine.nodes.review import _ddl_column_alignment_issues

        return _ddl_column_alignment_issues(
            _make_state() | {"sql_content": sql, "ddl_content": ddl}
        )

    def test_ddl_table_without_insert_is_critical(self):
        sql = (
            "insert overwrite table ads_dw_demo.ads_knight_weekly_di partition (inc_day = '1')\n"
            "select\n    emp_code,\n    dept_code\n"
            "from tmp_dw_demo.tmp_base\nwhere inc_day = '1';"
        )
        issues = self._issues(sql)
        assert len(issues) == 1, "hour 表缺 insert 应报 1 条 Critical"
        assert issues[0]["severity"] == "Critical"
        assert "ads_knight_weekly_hour_di" in issues[0]["message"], "点名缺交付的表"
        assert "无对应 insert overwrite" in issues[0]["message"]

    def test_all_ddl_tables_inserted_passes(self):
        sql = (
            "insert overwrite table ads_dw_demo.ads_knight_weekly_di partition (inc_day = '1')\n"
            "select\n    emp_code,\n    dept_code\nfrom tmp_dw_demo.tmp_base\nwhere inc_day = '1';\n"
            "insert overwrite table ads_dw_demo.ads_knight_weekly_hour_di partition (inc_day = '1')\n"
            "select\n    emp_code,\n    hour_bucket\nfrom tmp_dw_demo.tmp_base\nwhere inc_day = '1';"
        )
        assert self._issues(sql) == []

    def test_tmp_prefix_table_without_insert_skipped(self):
        ddl = """
        create table if not exists tmp_dw_demo.tmp_knight_base (
            emp_code string comment '员工编码'
        );
        create table if not exists ads_dw_demo.ads_knight_weekly_di (
            emp_code string comment '员工编码'
        ) partitioned by (inc_day string);
        """
        sql = (
            "insert overwrite table ads_dw_demo.ads_knight_weekly_di partition (inc_day = '1')\n"
            "select\n    emp_code\nfrom dwd.src\nwhere inc_day = '1';"
        )
        assert self._issues(sql, ddl) == [], "tmp_ 前缀表由 CTAS 落地，不要求 insert"
