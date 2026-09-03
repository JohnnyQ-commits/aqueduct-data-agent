"""P1-1: Phase 1 三合一拆分为 A（需求+方案）∥ B（DDL）并行小调用。

背景: design_ddl 三合一 completion 21652 tokens 贴近螺旋阈值 24000，
拆分后 A/B 各自思考密度减半。B 失败或与设计方案字段映射不一致时
回退既有 Phase 3 独立生成节点（ddl_content 置空即可）。
"""

from __future__ import annotations

from unittest.mock import patch

# ---------- 测试素材 ----------

_A_RESPONSE = """## 需求理解摘要

- 目标表: dm.city_daily_stats
- 数据来源: dwd.order_detail
- 过滤条件: status = 'valid'
- 关联关系: 无（单表聚合）
- 核心指标: order_cnt = COUNT(order_id)
- 表结构状态: 已获取

### 待确认问题

无

## 设计方案

### 取数逻辑

- 数据来源: dwd.order_detail
- 过滤条件: status = 'valid' AND inc_day = '${bizdate}'
- 关联关系: 无（单表 GROUP BY city）

### 字段映射

| 目标字段 | 源字段 | 转换逻辑 |
|----------|--------|----------|
| city | city | 直接映射，GROUP BY 键 |
| order_cnt | order_id | COUNT(order_id) |

### 上下游依赖

- 上游: dwd.order_detail
- 下游: 无（待确认）
"""

# 缺 "### 上下游依赖" —— 触发 P0-2 结构门禁重生成
_A_RESPONSE_BAD = _A_RESPONSE.replace("### 上下游依赖", "### 依赖关系说明")

# 字段映射章节在（过 P0-2 门禁）但无表格 —— 一致性校验应跳过（零误报原则）
_A_RESPONSE_NO_MAPPING = _A_RESPONSE.replace(
    """| 目标字段 | 源字段 | 转换逻辑 |
|----------|--------|----------|
| city | city | 直接映射，GROUP BY 键 |
| order_cnt | order_id | COUNT(order_id) |""",
    "city 直接映射自源表 city 字段；order_cnt 为 COUNT(order_id) 聚合指标。",
)

_B_RESPONSE = """目标表 DDL 如下：

```sql
CREATE TABLE IF NOT EXISTS dm.city_daily_stats (
    city string COMMENT '城市名称',
    order_cnt bigint COMMENT '有效订单数量'
)
COMMENT '每日各城市有效订单数量统计'
PARTITIONED BY (inc_day string COMMENT '分区日期，格式 YYYYMMDD')
STORED AS PARQUET
;
```
"""

# DDL 缺 city 字段 —— 与 A 设计方案字段映射不一致
_B_RESPONSE_NO_CITY = """目标表 DDL 如下：

```sql
CREATE TABLE IF NOT EXISTS dm.city_daily_stats (
    order_cnt bigint COMMENT '有效订单数量'
)
COMMENT '每日各城市有效订单数量统计'
PARTITIONED BY (inc_day string COMMENT '分区日期，格式 YYYYMMDD')
STORED AS PARQUET
;
```
"""

# 无 SQL 代码块 —— B 响应无效
_B_RESPONSE_NO_BLOCK = "抱歉，我无法生成 DDL，表结构信息不足。"


def _req_state() -> dict:
    return {
        "requirement": "统计每日各城市订单数量",
        "mode": "dev",
        "metadata": {"requirement_name": "split_test"},
        "errors": [],
        "artifacts": [],
    }


def _run_split(
    state: dict,
    a_responses: list[str],
    b_responses: list[str],
    b_raises: bool = False,
) -> tuple[list[tuple[str, str]], dict[str, str], list[str], dict]:
    """跑 node_requirement（A∥B 拆分路径）。

    Returns:
        (call_llm 调用记录 (task_type, prompt), 落盘产物, errors, 最终 state)
    """
    from src.aqueduct.engine.nodes.requirement import node_requirement

    calls: list[tuple[str, str]] = []
    saved: dict[str, str] = {}

    def fake_llm(st, task_type, prompt):
        calls.append((task_type, prompt))
        if task_type == "design_ddl":
            return a_responses[
                min(len([c for c in calls if c[0] == "design_ddl"]) - 1, len(a_responses) - 1)
            ]
        # ddl_gen：修复调用（prompt 带一致性警示）取第二个响应
        is_fix = "一致性警示" in prompt
        idx = 1 if is_fix else 0
        if b_raises:
            raise RuntimeError("LLM 网关不可用")
        return b_responses[min(idx, len(b_responses) - 1)]

    def fake_save(st, filename, content):
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

    return calls, saved, state.get("errors", []), state


# ---------- 纯函数：字段提取与一致性比对 ----------


class TestExtractMappingFields:
    """设计方案「字段映射」表格的目标字段提取。"""

    def test_standard_table(self):
        from src.aqueduct.engine.nodes.requirement import _extract_mapping_fields

        assert _extract_mapping_fields(_A_RESPONSE) == {"city", "order_cnt"}

    def test_missing_section_returns_empty(self):
        from src.aqueduct.engine.nodes.requirement import _extract_mapping_fields

        # 章节在但无表格
        assert _extract_mapping_fields(_A_RESPONSE_NO_MAPPING) == set()
        # 章节整体缺失
        assert _extract_mapping_fields("## 设计方案\n\n### 取数逻辑\n单表聚合。") == set()

    def test_section_stops_at_next_heading(self):
        """映射表后跟其他章节（含新的表格）时不越界提取。"""
        from src.aqueduct.engine.nodes.requirement import _extract_mapping_fields

        design = _A_RESPONSE + "\n### 调度配置\n\n| 参数 | 值 |\n|------|------|\n| 周期 | 日 |\n"
        assert _extract_mapping_fields(design) == {"city", "order_cnt"}

    def test_type_annotated_cells(self):
        """真实输出首列常带类型标注（order_count (bigint)）——取首 token 提取。"""
        from src.aqueduct.engine.nodes.requirement import _extract_mapping_fields

        design = (
            "### 字段映射\n\n"
            "| 目标字段 | 源字段 | 转换逻辑 |\n"
            "|----------|--------|----------|\n"
            "| inc_day (string, 分区) | '${bizdate}' | 调度参数经 PARTITION 子句写入 |\n"
            "| order_count (bigint) | order_id | COUNT(DISTINCT order_id) |\n"
        )
        assert _extract_mapping_fields(design) == {"inc_day", "order_count"}


class TestExtractDdlFields:
    """CREATE TABLE 语句的列字段提取（含分区字段）。"""

    def test_standard_ddl_with_partition(self):
        from src.aqueduct.engine.nodes.requirement import _extract_ddl_fields

        assert _extract_ddl_fields(_B_RESPONSE) == {"city", "order_cnt", "inc_day"}

    def test_garbage_returns_empty(self):
        from src.aqueduct.engine.nodes.requirement import _extract_ddl_fields

        assert _extract_ddl_fields("这不是 DDL 文本") == set()


class TestCheckDdlConsistency:
    """设计方案字段映射 vs DDL 字段集比对。"""

    def test_consistent_returns_empty(self):
        from src.aqueduct.engine.nodes.requirement import _check_ddl_consistency

        assert _check_ddl_consistency(_A_RESPONSE, _B_RESPONSE) == []

    def test_ddl_missing_field_reported(self):
        from src.aqueduct.engine.nodes.requirement import _check_ddl_consistency

        assert _check_ddl_consistency(_A_RESPONSE, _B_RESPONSE_NO_CITY) == ["city"]

    def test_ddl_extra_field_ok(self):
        """DDL 比映射多的字段（etl_time 等）不报缺 —— 只查映射→DDL 方向。"""
        from src.aqueduct.engine.nodes.requirement import _check_ddl_consistency

        ddl_extra = _B_RESPONSE.replace(
            "    order_cnt bigint COMMENT '有效订单数量'",
            "    order_cnt bigint COMMENT '有效订单数量',\n    etl_time string COMMENT 'ETL 时间'",
        )
        assert _check_ddl_consistency(_A_RESPONSE, ddl_extra) == []

    def test_unparseable_mapping_skips(self):
        """映射提取失败 → 跳过校验（零误报）。"""
        from src.aqueduct.engine.nodes.requirement import _check_ddl_consistency

        assert _check_ddl_consistency(_A_RESPONSE_NO_MAPPING, _B_RESPONSE_NO_CITY) == []

    def test_unparseable_ddl_skips(self):
        """DDL 提取失败 → 跳过校验。"""
        from src.aqueduct.engine.nodes.requirement import _check_ddl_consistency

        assert _check_ddl_consistency(_A_RESPONSE, "garbage") == []


# ---------- 集成：node_requirement A∥B 拆分 ----------


class TestNodeRequirementSplit:
    """node_requirement 拆分路径：A（design_ddl）∥ B（ddl_gen）。"""

    def test_two_task_types_called(self):
        """A/B 各 1 次调用，Phase1/2/3 产物齐全。"""
        calls, saved, errors, state = _run_split(_req_state(), [_A_RESPONSE], [_B_RESPONSE])

        task_types = [c[0] for c in calls]
        assert task_types.count("design_ddl") == 1
        assert task_types.count("ddl_gen") == 1
        assert errors == []
        assert "Phase1-需求理解摘要.md" in saved
        assert "Phase2-设计方案.md" in saved
        assert "Phase3-表结构.sql" in saved
        assert state["metadata"]["ddl_done"] == "true"

    def test_ddl_comes_from_b_not_a(self):
        """A 响应无 SQL 块，Phase3 DDL 来自 B 调用。"""
        assert "```sql" not in _A_RESPONSE  # 前置确认

        saved = _run_split(_req_state(), [_A_RESPONSE], [_B_RESPONSE])[1]

        ddl = saved["Phase3-表结构.sql"]
        assert "CREATE TABLE IF NOT EXISTS dm.city_daily_stats" in ddl
        assert "city string" in ddl
        assert "PARTITIONED BY (inc_day" in ddl

    def test_b_exception_falls_back_to_phase3(self):
        """B 调用异常 → 不炸管道，ddl_done=false，errors 记录回退。"""
        _, saved, errors, state = _run_split(_req_state(), [_A_RESPONSE], [], b_raises=True)

        assert state["metadata"]["ddl_done"] == "false"
        assert "Phase3-表结构.sql" not in saved
        assert len(errors) == 1
        assert "回退" in errors[0]

    def test_b_no_sql_block_falls_back(self):
        """B 响应无 SQL 代码块 → 视为失败回退。"""
        _, saved, errors, state = _run_split(_req_state(), [_A_RESPONSE], [_B_RESPONSE_NO_BLOCK])

        assert state["metadata"]["ddl_done"] == "false"
        assert "Phase3-表结构.sql" not in saved
        assert any("回退" in e for e in errors)

    def test_inconsistent_ddl_one_fix_recovers(self):
        """DDL 缺映射字段 → 1 次定向修复 → 一致落盘，无 errors。"""
        calls, saved, errors, state = _run_split(
            _req_state(), [_A_RESPONSE], [_B_RESPONSE_NO_CITY, _B_RESPONSE]
        )

        ddl_calls = [c for c in calls if c[0] == "ddl_gen"]
        assert len(ddl_calls) == 2
        assert "一致" in ddl_calls[1][1]  # 修复 prompt 带一致性警示
        assert "city string" in saved["Phase3-表结构.sql"]
        assert errors == []
        assert state["metadata"]["ddl_done"] == "true"

    def test_inconsistent_fix_fails_falls_back(self):
        """修复后仍缺字段 → 丢弃 B 回退 Phase 3，errors 记录。"""
        calls, saved, errors, state = _run_split(
            _req_state(), [_A_RESPONSE], [_B_RESPONSE_NO_CITY, _B_RESPONSE_NO_CITY]
        )

        ddl_calls = [c for c in calls if c[0] == "ddl_gen"]
        assert len(ddl_calls) == 2
        assert state["metadata"]["ddl_done"] == "false"
        assert "Phase3-表结构.sql" not in saved
        assert len(errors) == 1
        assert "city" in errors[0]
        assert "回退" in errors[0]

    def test_gate_retries_a_only(self):
        """A 缺章 → 门禁只重生成 A，B 不重试。"""
        calls, saved, errors, _ = _run_split(
            _req_state(), [_A_RESPONSE_BAD, _A_RESPONSE], [_B_RESPONSE]
        )

        task_types = [c[0] for c in calls]
        assert task_types.count("design_ddl") == 2
        assert task_types.count("ddl_gen") == 1
        assert errors == []
        assert "### 上下游依赖" in saved["Phase2-设计方案.md"]

    def test_unparseable_mapping_skips_consistency(self):
        """A 无映射表 → 跳过一致性校验，B 原样落盘。"""
        calls, saved, errors, state = _run_split(
            _req_state(), [_A_RESPONSE_NO_MAPPING], [_B_RESPONSE_NO_CITY]
        )

        task_types = [c[0] for c in calls]
        assert task_types.count("ddl_gen") == 1
        assert "Phase3-表结构.sql" in saved
        assert state["metadata"]["ddl_done"] == "true"
        assert errors == []
