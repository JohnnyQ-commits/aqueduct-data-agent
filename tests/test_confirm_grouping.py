"""跨维 Confirm 分组去重测试（审查降噪）。

背景（perf11-check2 实录）：单块审查拆 3 维度并行后，同一口径问题被不同
维度各自提出——20 条 Confirm 实际独立议题仅 ~10 条（目标表冲突×3、状态码
字典×2、GMV 口径×2、去重/关联键×2、归日口径×2、客单价分母×2、维表粒度×3），
交付总报告的待确认清单噪音翻倍。

方案：确定性锚点聚类（P0-1 零 token 先行哲学）——两条目共享「稀有」标识符
（表名/字段名 snake_case token，全局出现 ≤3 条目）即同组；best-match 贪心
（共享数最多的组优先）防链式吞并。零信息丢失：组内子条目全文保留，只改
呈现不改 state 契约（review_confirmations 仍为扁平列表，evals 计数不变）。
"""

from __future__ import annotations

from src.aqueduct.engine.nodes.report import _group_confirmations


def _c(msg: str) -> dict[str, str]:
    return {"severity": "Confirm", "message": msg}


# 取自 perf11-check2 实录（措辞精简，标识符原样保留）
_FIXTURE = [
    _c(
        "(L19) 输入目标表 dw_demo.dwd_order_info_di 与需求文档 dw_demo.dws_order_daily_stat_di 冲突，SQL 依需求文档取后者"
    ),
    _c("(L33/L39) 订单去重键暂取 inner_order_no（候选 src_order_no），键选错直接影响订单数口径"),
    _c("(L55) 客户关联键暂取 client_code=customer_code（候选 member_id=customer_id）"),
    _c(
        "(L35/L41) GMV 暂取 declared_value_amt 声明价值（候选 cod_amt/est_price），与需求实付金额口径未必等价"
    ),
    _c(
        "(L44-45) 状态过滤 cast(order_status_code as bigint)>=20 且 is_cancel_flag=0，状态码字典未知"
    ),
    _c("(L24-29) 客单价分母暂取 order_count、round 保留 2 位，成交订单数口径与精度未确认"),
    _c("(L43) 当日归属按 inc_day 分区归日而非 order_tm/create_tm 时间戳归日，跨日订单口径未确认"),
    _c(
        "(L52-53) dim_customer_info_df 取 '${bizdate}' 当日快照、LEFT JOIN 未匹配订单不计入 customer_count 均为假设"
    ),
    _c(
        "(L47-55) dim_customer_info_df 当日快照内 customer_code 唯一性未确认，一人多行将放大 SUM(gmv)"
    ),
    _c("(L42) 源表字段呈物流运单特征（waybill_no/cargo_*），dwd_order_info_di 表选型存疑"),
    _c(
        "(L20) 输入参数目标表 dw_demo.dwd_order_info_di 与需求文档 dw_demo.dws_order_daily_stat_di 冲突，SQL 按需求文档落地"
    ),
    _c(
        "(L45-46) cast(order_status_code as bigint) 会把非数字状态码静默过滤，is_cancel_flag=0 连带过滤 NULL 行"
    ),
    _c("(L36/L42) GMV 以 declared_value_amt 代替实付金额汇总，金额口径存在实质偏差"),
    _c(
        "(L34/L40/L56) 订单去重键 inner_order_no 与维表关联键 client_code=customer_code 均为候选假设"
    ),
    _c(
        "(L48-56) dim_customer_info_df 取 '${bizdate}' 快照及分区内唯一均为假设，JOIN 扇出放大 sum(gmv)"
    ),
    _c(
        "(L34-36) count(distinct inner_order_no) 去重但 sum(declared_value_amt) 未去重，分区粒度一单多行将重复累计"
    ),
    _c("(L44) 当日归属按 inc_day 分区归日而非时间戳归日，跨日订单归属可能不同"),
    _c("(L25-30) 客单价分母取 order_count 且 round 后存 decimal(18,4)，口径与精度未拍板"),
    _c(
        "(Line 23) 输入参数目标表名 dw_demo.dwd_order_info_di 与需求文档冲突，须在调度落配置前消除歧义"
    ),
    _c("(Line 59) client_code=customer_code 两侧字段类型未核实，需对照 DDL 补显式 CAST"),
]


class TestGroupConfirmations:
    def test_cross_dimension_duplicates_merge(self):
        """跨维重复项按稀有标识符聚组：目标表/状态码/GMV/归日/客单价/维表。"""
        groups = _group_confirmations(_FIXTURE)
        texts_per_group = [[m["message"] for m in g["members"]] for g in groups]
        joined = ["|".join(ts) for ts in texts_per_group]

        # 目标表冲突 ×3 同组
        assert any(sum("与需求文档" in t for t in ts) >= 2 for ts in texts_per_group)
        # 状态码字典 ×2 同组
        assert any(sum("order_status_code" in t for t in ts) >= 2 for ts in texts_per_group)
        # GMV 口径 ×2 同组
        assert any(sum("declared_value_amt" in t for t in ts) >= 2 for ts in texts_per_group)
        # 归日口径 ×2 同组（inc_day）
        assert any(sum("inc_day" in t for t in ts) >= 2 for ts in texts_per_group)
        # 客单价分母 ×2 同组（avg_order_amount/order_count+round）
        assert any(sum("客单价" in t for t in ts) >= 2 for ts in texts_per_group)
        # 维表粒度 ×2+ 同组（dim_customer_info_df）
        assert any(sum("dim_customer_info_df" in t for t in ts) >= 2 for ts in texts_per_group)
        # 去重键 ×2 同组（inner_order_no）
        assert any(sum("inner_order_no" in t for t in ts) >= 2 for ts in texts_per_group)
        # 全部组数显著小于条目数（降噪到 ~13 以内）
        assert len(groups) <= 13, joined

    def test_no_information_loss(self):
        """零信息丢失：所有原始 message 都还在（组代表或组内子条目）。"""
        groups = _group_confirmations(_FIXTURE)
        kept = [m["message"] for g in groups for m in g["members"]]
        assert sorted(kept) == sorted(m["message"] for m in _FIXTURE)

    def test_single_item_passthrough(self):
        """单条目输入：原样成组（代表=自己）。"""
        groups = _group_confirmations([_c("独立问题 foo_bar 说明")])
        assert len(groups) == 1
        assert groups[0]["representative"]["message"] == "独立问题 foo_bar 说明"
        assert len(groups[0]["members"]) == 1

    def test_empty_input(self):
        assert _group_confirmations([]) == []

    def test_common_tokens_do_not_chain_merge(self):
        """高频 token（bizdate/select 等出现在 >3 条目）不触发合并。"""
        items = [
            _c("(a) x1 取 '${bizdate}' 快照假设 alpha_key 口径"),
            _c("(b) y2 取 '${bizdate}' 快照假设 beta_key 口径"),
            _c("(c) z3 取 '${bizdate}' 快照假设 gamma_key 口径"),
            _c("(d) w4 取 '${bizdate}' 快照假设 delta_key 口径"),
        ]
        groups = _group_confirmations(items)
        # bizdate 出现在 4 条（>3 非稀有）——不得全部链成一组
        assert len(groups) >= 2
