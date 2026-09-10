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

from unittest.mock import patch

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
        "ddl_content": _DDL,
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
            {"severity": "Confirm", "message": "源表无实付金额字段，gmv 口径需业务确认"}
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
            {"severity": "Critical", "message": "除法未判零 (line 42) — 加 nullif"}
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
        ):
            node_review(state)

        assert len(prompts) == 2, "2 块 = 2 次调用（非 3 维度拆分）"
        assert "[审查第 1/2 个 SQL 语句块]" in prompts[0]
