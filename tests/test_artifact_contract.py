"""P0-2 交付物结构契约测试。

契约与 prompt 模板的输出格式指令**对齐**（模板要求的章节 = 契约校验的章节）：
合法素材直接取自各模板「输出格式/示例」区，缺陷素材取自 v5 管道实际输出
（Phase2-设计方案.md 仅含单个标题，LLM 偏离模板契约且无校验直接落盘）。

门禁三段式：校验 → 缺章触发 1 次定向重生成 → 仍缺则横幅降级 + errors 记录（不死管道）。
"""

from __future__ import annotations

from src.aqueduct.engine.contract import (
    build_retry_prompt,
    ensure_structure,
    scan_degradation,
    validate_structure,
)

# ---------------------------------------------------------------------------
# 合法素材：与模板示例输出对齐
# ---------------------------------------------------------------------------

# requirement_and_design.tpl.md「输出格式 · 第一部分」示例
VALID_PHASE1 = """- 目标表: dm.city_daily_stats
- 数据来源: dwd.order_detail
- 过滤条件: status = 'valid'（有效订单）、inc_day = '$[0]'（分区过滤）
- 关联关系: 无（单表聚合）
- 核心指标: order_count = COUNT(order_id)
- 表结构状态: 已获取

### 待确认问题
无
"""

# requirement_and_design.tpl.md「输出格式 · 第二部分」示例（OPT-5 三合一，H3 小节）
VALID_PHASE2_OPT5 = """## 设计方案

### 取数逻辑

- 数据来源: dwd.order_detail
- 过滤条件: status = 'valid' AND inc_day = '$[0]'

### 字段映射

| 目标字段 | 源字段 | 转换逻辑 |
|----------|--------|----------|
| city | city | 直接映射，GROUP BY 键 |

### 上下游依赖

- 上游: dwd.order_detail
- 下游: 无（待确认）
"""

# design_ddl.tpl.md「输出格式 · 第一部分」示例（OPT-4 二合一，H2 直出无总标题）
VALID_PHASE2_OPT4 = """## 取数逻辑

- 数据来源: dwd.order_detail
- 过滤条件: inc_day = '$[0]'（分区过滤）

## 字段映射

| 目标字段 | 源字段 | 转换逻辑 |
|----------|--------|----------|

## 上下游依赖

- 上游: dwd.order_detail
"""

# report_delivery.tpl.md「推理步骤 6」的 Design.md 章节要求
# （需求背景 / 设计方案 / 表结构 / 核心 SQL / 血缘图）
VALID_DESIGN_DOC = """# 每日订单统计 — 设计文档

## 需求背景

统计每日各城市订单量。

## 设计方案

单表聚合。

## 表结构(DDL)

```sql
CREATE TABLE IF NOT EXISTS dm.city_daily_stats (city string, order_cnt bigint);
```

## 核心 SQL

```sql
INSERT OVERWRITE TABLE dm.city_daily_stats
SELECT city, count(*) FROM dwd.order_detail WHERE inc_day = '$[0]' GROUP BY city;
```

## 血缘图

```mermaid
graph LR
  A[dwd.order_detail] --> B[dm.city_daily_stats]
```
"""

# knowledge_extract.tpl.md「任务」的 5 章结构
VALID_KNOWLEDGE = """# 知识沉淀 — 每日订单统计

### 一、业务域知识

核心实体: Order (order_id, city, inc_day)

### 二、表结构经验

分区字段统一 inc_day。

### 三、SQL 开发经验

宽表模式。

### 四、指标口径

order_count = COUNT(order_id)。

### 五、待确认 / 待沉淀事项

枚举值待验证。
"""


class TestValidateStructure:
    """validate_structure：必需章节正则校验。"""

    def test_valid_phase1_passes(self):
        """模板示例 Phase1（目标表列表 + 待确认问题）→ 无缺失。"""
        assert validate_structure("Phase1-需求理解摘要.md", VALID_PHASE1) == []

    def test_phase1_missing_confirm_section(self):
        """缺「待确认问题」章节 → 报缺失。"""
        content = VALID_PHASE1.split("### 待确认问题")[0]
        missing = validate_structure("Phase1-需求理解摘要.md", content)
        assert "待确认问题" in missing

    def test_phase1_missing_target_table(self):
        """缺「目标表」列表项 → 报缺失。"""
        content = "\n".join(line for line in VALID_PHASE1.splitlines() if "目标表" not in line)
        missing = validate_structure("Phase1-需求理解摘要.md", content)
        assert "目标表" in missing

    def test_valid_phase2_both_template_styles(self):
        """两条模板路径的示例输出都通过（OPT-5 H3 小节 / OPT-4 H2 直出）。"""
        assert validate_structure("Phase2-设计方案.md", VALID_PHASE2_OPT5) == []
        assert validate_structure("Phase2-设计方案.md", VALID_PHASE2_OPT4) == []

    def test_phase2_v5_single_heading_intercepted(self):
        """v5 实际缺陷样本：仅有 '## 设计方案' 单标题，三个必需小节全缺。"""
        missing = validate_structure("Phase2-设计方案.md", "## 设计方案\n\n文本内容。")
        assert missing == ["取数逻辑", "字段映射", "上下游依赖"]

    def test_valid_design_doc_passes(self):
        """模板要求的 Design.md 五章节齐全 → 无缺失。"""
        assert validate_structure("Phase6-Design.md", VALID_DESIGN_DOC) == []

    def test_design_doc_missing_lineage(self):
        """缺血缘图（标题与 mermaid 块均无）→ 报缺失。"""
        content = VALID_DESIGN_DOC.replace("## 血缘图", "## 数据流").replace("mermaid", "text")
        missing = validate_structure("Phase6-Design.md", content)
        assert "血缘图" in missing

    def test_design_doc_mermaid_block_satisfies_lineage(self):
        """无「血缘图」标题但有 mermaid 块 → 血缘关系已表达，不报缺失。"""
        content = VALID_DESIGN_DOC.replace("## 血缘图", "## 数据流")
        assert validate_structure("Phase6-Design.md", content) == []

    def test_valid_knowledge_doc_passes(self):
        """知识沉淀 5 章结构齐全 → 无缺失。"""
        assert validate_structure("Phase6-知识沉淀.md", VALID_KNOWLEDGE) == []

    def test_knowledge_doc_missing_chapters(self):
        """缺「指标口径」「待确认」章节 → 报缺失。"""
        content = VALID_KNOWLEDGE.split("### 四、指标口径")[0]
        missing = validate_structure("Phase6-知识沉淀.md", content)
        assert "指标口径" in missing
        assert "待确认事项" in missing

    def test_unknown_artifact_not_checked(self):
        """未定义契约的交付物（SQL/内部报告）不校验，返回空。"""
        assert validate_structure("Phase3-表结构.sql", "CREATE TABLE t;") == []
        assert validate_structure("Phase4-SQL校验报告.md", "随便什么") == []

    def test_empty_content_not_checked(self):
        """空内容不校验（空响应由 call_llm 层重试，契约层不拦）。"""
        assert validate_structure("Phase2-设计方案.md", "") == []
        assert validate_structure("Phase2-设计方案.md", "   \n") == []


class TestEnsureStructure:
    """ensure_structure：校验 → 1 次定向重生成 → 仍缺横幅降级。"""

    def test_valid_passthrough(self):
        """结构合规 → 原样返回，不调用重生成。"""
        calls: list[list[str]] = []

        def regenerate(missing):
            calls.append(missing)
            return VALID_PHASE2_OPT5

        content, missing = ensure_structure("Phase2-设计方案.md", VALID_PHASE2_OPT5, regenerate)
        assert missing == []
        assert content == VALID_PHASE2_OPT5
        assert calls == []

    def test_regenerate_recovers(self):
        """缺章 → 重生成补全 → 复检通过 → 返回新内容。"""
        bad = "## 设计方案\n\n只有总体说明。"
        calls: list[list[str]] = []

        def regenerate(missing):
            calls.append(missing)
            return VALID_PHASE2_OPT5

        content, missing = ensure_structure("Phase2-设计方案.md", bad, regenerate)
        assert missing == []
        assert content == VALID_PHASE2_OPT5
        # 重生成回调收到的是缺章清单
        assert calls == [["取数逻辑", "字段映射", "上下游依赖"]]

    def test_still_missing_degrades_with_banner(self):
        """重生成后仍缺 → 内容头部加横幅降级，缺失清单返回（不死管道）。"""
        bad = "## 设计方案\n\n只有总体说明。"
        worse = "## 设计方案\n\n还是缺小节。"

        def regenerate(missing):
            return worse

        content, missing = ensure_structure("Phase2-设计方案.md", bad, regenerate)
        assert missing == ["取数逻辑", "字段映射", "上下游依赖"]
        assert content.startswith("> ⚠️")
        assert "取数逻辑" in content.splitlines()[0]
        # 降级内容 = 横幅 + 重生成版本（取较新的一次）
        assert worse in content

    def test_no_regenerate_degrades_directly(self):
        """无重生成回调（调用方不支持重试）→ 直接横幅降级。"""
        bad = "## 设计方案\n\n只有总体说明。"
        content, missing = ensure_structure("Phase2-设计方案.md", bad)
        assert missing == ["取数逻辑", "字段映射", "上下游依赖"]
        assert content.startswith("> ⚠️")
        assert bad in content

    def test_regenerate_exception_degrades(self):
        """重生成回调抛异常 → 捕获降级（原内容 + 横幅），不向上抛。"""

        def regenerate(missing):
            raise RuntimeError("网关炸了")

        bad = "## 设计方案\n\n只有总体说明。"
        content, missing = ensure_structure("Phase2-设计方案.md", bad, regenerate)
        assert missing == ["取数逻辑", "字段映射", "上下游依赖"]
        assert content.startswith("> ⚠️")
        assert bad in content


class TestBuildRetryPrompt:
    """build_retry_prompt：定向重生成提示拼接。"""

    def test_hint_appended_to_original_prompt(self):
        """原 prompt 完整保留，缺章清单与文件名出现在警示区。"""
        prompt = "你是一名资深数据仓库架构师。"
        retry = build_retry_prompt(prompt, "Phase2-设计方案.md", ["取数逻辑", "字段映射"])
        assert prompt in retry
        assert "取数逻辑" in retry
        assert "字段映射" in retry
        assert "Phase2-设计方案.md" in retry
        assert retry.index(prompt) < retry.index("取数逻辑")


class TestScanDegradation:
    """scan_degradation：横幅标记扫描（供节点统一记 errors）。"""

    def test_finds_banner_lines(self):
        content = "正文第一行\n> ⚠️ **结构门禁告警**：本文档缺少必需章节——血缘图。\n正文最后一行"
        found = scan_degradation(content)
        assert len(found) == 1
        assert "血缘图" in found[0]

    def test_no_banner_returns_empty(self):
        assert scan_degradation(VALID_DESIGN_DOC) == []


# ---------------------------------------------------------------------------
# 节点集成：门禁接入 node_requirement / node_report
# ---------------------------------------------------------------------------

# 三合一完整响应（requirement_and_design.tpl.md 输出格式）
_FULL_TRIPLE = (
    "## 需求理解摘要\n\n"
    "- 目标表: dm.city_daily_stats\n"
    "- 数据来源: dwd.order_detail\n\n"
    "### 待确认问题\n无\n\n"
    "## 设计方案\n\n"
    "### 取数逻辑\n- 数据来源: dwd.order_detail\n\n"
    "### 字段映射\n| 目标字段 | 源字段 | 转换逻辑 |\n\n"
    "### 上下游依赖\n- 上游: dwd.order_detail\n\n"
    "```sql\nCREATE TABLE dm.city_daily_stats (city string COMMENT '城市');\n```\n"
)

# v5 风格缺章响应：Phase1 缺待确认问题，Phase2 缺三个小节
_BAD_TRIPLE = (
    "## 需求理解摘要\n\n"
    "- 目标表: dm.city_daily_stats\n\n"
    "## 设计方案\n\n"
    "单表聚合，具体字段见 DDL。\n\n"
    "```sql\nCREATE TABLE dm.city_daily_stats (city string COMMENT '城市');\n```\n"
)


def _req_state() -> dict:
    return {
        "requirement": "统计每日各城市订单数量",
        "mode": "dev",
        "metadata": {"requirement_name": "contract_gate_test"},
        "errors": [],
        "artifacts": [],
    }


def _run_requirement(state, responses: list[str]) -> tuple[list[str], dict[str, str], list[str]]:
    """跑 node_requirement，返回 (design_ddl 调用 prompts, saved artifacts, errors)。

    P1-1 拆分后 node_requirement 固定 A(design_ddl) ∥ B(ddl_gen) 两次调用，
    这里只统计 design_ddl（门禁路径）；ddl_gen 返回同源响应（含 sql 块可提取，
    且两 fixture 的映射表无数据行 → 一致性校验跳过，不干扰 errors 断言）。
    """
    from unittest.mock import patch

    from src.aqueduct.engine.nodes.requirement import node_requirement

    prompts: list[str] = []
    saved: dict[str, str] = {}

    def fake_llm(state, task_type, prompt):
        if task_type == "design_ddl":
            prompts.append(prompt)
            return responses[min(len(prompts) - 1, len(responses) - 1)]
        return responses[-1]

    def fake_save(state, filename, content):
        saved[filename] = content
        return filename

    with (
        patch("src.aqueduct.engine.nodes.requirement._recall_domain_knowledge"),
        patch("src.aqueduct.engine.nodes.requirement._query_table_schemas", return_value={}),
        patch("src.aqueduct.engine.nodes.requirement.get_skill") as mock_skill,
        patch("src.aqueduct.engine.nodes.requirement.call_llm", side_effect=fake_llm),
        patch("src.aqueduct.engine.nodes.requirement.save_artifact", side_effect=fake_save),
    ):
        mock_skill.return_value.execute.return_value = type(
            "R", (), {"success": True, "data": {"prompt": "基础 prompt"}, "error": ""}
        )()
        node_requirement(state)

    return prompts, saved, state["errors"]


class TestNodeRequirementGate:
    """node_requirement 三合一响应的结构门禁。"""

    def test_contract_pass_no_retry(self):
        """响应符合契约 → 单次调用，落盘无横幅，errors 无记录。"""
        prompts, saved, errors = _run_requirement(_req_state(), [_FULL_TRIPLE])

        assert len(prompts) == 1
        assert errors == []
        assert not saved["Phase1-需求理解摘要.md"].startswith(">")
        assert not saved["Phase2-设计方案.md"].startswith(">")
        assert "待确认问题" in saved["Phase1-需求理解摘要.md"]

    def test_missing_sections_retry_then_recover(self):
        """首响应缺章 → 定向重生成 1 次（prompt 带结构警示）→ 补全落盘，无 errors。"""
        prompts, saved, errors = _run_requirement(_req_state(), [_BAD_TRIPLE, _FULL_TRIPLE])

        assert len(prompts) == 2
        assert "结构警示" in prompts[1]
        assert errors == []
        assert "### 字段映射" in saved["Phase2-设计方案.md"]
        assert not saved["Phase2-设计方案.md"].startswith(">")

    def test_retry_still_missing_degrades(self):
        """重生成仍缺 → 横幅降级落盘 + errors 记录（管道不死）。"""
        prompts, saved, errors = _run_requirement(_req_state(), [_BAD_TRIPLE, _BAD_TRIPLE])

        assert len(prompts) == 2
        assert saved["Phase2-设计方案.md"].startswith("> ⚠️")
        assert any("结构缺章" in e for e in errors)
        # Phase1 也缺章（待确认问题），同样降级
        assert saved["Phase1-需求理解摘要.md"].startswith("> ⚠️")


def _report_state() -> dict:
    return {
        "requirement": "需求文档",
        "requirement_summary": "需求摘要",
        "design_scheme": "设计方案",
        "ddl_content": "CREATE TABLE t (id bigint)",
        "sql_content": "SELECT 1",
        "review_result": "审查通过",
        "dqc_result": {"results": []},
        "validation_result": {"issues": []},
        "lineage_result": {"sources": ["t"], "mermaid": "graph LR"},
        "domain_context": "域上下文",
        "metadata": {"requirement_name": "report_gate_test"},
        "errors": [],
        "artifacts": [],
    }


class TestNodeReportGate:
    """node_report Phase6 文档的结构门禁。"""

    @staticmethod
    def _run_report(doc_responses: list[str], kn_responses: list[str]):
        from unittest.mock import patch

        from src.aqueduct.engine.nodes.report import node_report

        state = _report_state()
        doc_prompts: list[str] = []
        kn_prompts: list[str] = []
        saved: dict[str, str] = {}

        def fake_llm(state, task_type, prompt):
            if task_type == "doc_gen":
                doc_prompts.append(prompt)
                return doc_responses[min(len(doc_prompts) - 1, len(doc_responses) - 1)]
            if task_type == "knowledge_extract":
                kn_prompts.append(prompt)
                return kn_responses[min(len(kn_prompts) - 1, len(kn_responses) - 1)]
            return "other"

        def fake_save(state, filename, content):
            saved[filename] = content
            return filename

        with (
            patch("src.aqueduct.engine.nodes.report.wait_for_lineage"),
            patch("src.aqueduct.engine.nodes.report.call_llm", side_effect=fake_llm),
            patch("src.aqueduct.engine.nodes.report.save_artifact", side_effect=fake_save),
            patch("src.aqueduct.tools.registry.get_tool"),
        ):
            node_report(state)

        return state, doc_prompts, kn_prompts, saved

    def test_design_doc_pass_no_retry(self):
        """doc_gen 响应符合 Design 契约 → 单次调用无降级。"""
        state, doc_prompts, _, saved = self._run_report([VALID_DESIGN_DOC], [VALID_KNOWLEDGE])

        assert len(doc_prompts) == 1
        assert not saved["Phase6-Design.md"].startswith(">")
        assert state["errors"] == []

    def test_design_doc_retry_then_recover(self):
        """doc_gen 首次缺血缘图 → 定向重生成补全 → 落盘干净无 errors。"""
        bad_doc = VALID_DESIGN_DOC.replace("## 血缘图", "## 数据流").replace("mermaid", "text")
        state, doc_prompts, _, saved = self._run_report(
            [bad_doc, VALID_DESIGN_DOC], [VALID_KNOWLEDGE]
        )

        assert len(doc_prompts) == 2
        assert "结构警示" in doc_prompts[1]
        assert not saved["Phase6-Design.md"].startswith(">")
        assert state["errors"] == []

    def test_design_doc_still_missing_degrades(self):
        """doc_gen 重生成仍缺 → 横幅落盘 + errors 记录。"""
        bad_doc = VALID_DESIGN_DOC.replace("## 血缘图", "## 数据流").replace("mermaid", "text")
        state, _, _, saved = self._run_report([bad_doc, bad_doc], [VALID_KNOWLEDGE])

        assert saved["Phase6-Design.md"].startswith("> ⚠️")
        assert any("Phase6-Design.md" in e and "缺章" in e for e in state["errors"])

    def test_knowledge_doc_retry_then_degrade(self):
        """knowledge_extract 缺章 → 重生成 1 次 → 仍缺 → 横幅落盘 + errors 记录。"""
        # 缺指标口径与待确认两章（保留 ≥100 字符不走 fallback）
        bad_kn = VALID_KNOWLEDGE.split("### 四、指标口径")[0] + "x" * 60
        state, _, kn_prompts, saved = self._run_report([VALID_DESIGN_DOC], [bad_kn, bad_kn])

        assert len(kn_prompts) == 2
        assert "结构警示" in kn_prompts[1]
        assert saved["Phase6-知识沉淀.md"].startswith("> ⚠️")
        assert any("Phase6-知识沉淀.md" in e and "缺章" in e for e in state["errors"])
