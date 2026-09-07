# 需求：电商订单日统计表新增退款指标（迭代）

## 背景

`dw_demo.dws_order_daily_stat_di`（订单日活统计表）已上线运行，业务侧需要监控退款趋势，要求在该表上新增退款指标。属于**存量表迭代需求**，必须保证既有指标口径不受影响。

## 需求描述

在现有 `dw_demo.dws_order_daily_stat_di` 表上新增以下指标字段：

1. **当日退款订单数** (refund_order_count) — 去重统计
2. **当日退款金额** (refund_amount) — 退款成功订单的退款金额汇总

## 数据来源

- 既有来源（口径不变）：
  - 订单主表：`dw_demo.dwd_order_info_di`
  - 客户维度表：`dw_demo.dim_customer_info_df`
- 新增来源：
  - 退款明细表：`dw_demo.dwd_order_refund_info_di`

## 统计口径

- **统计周期**：按天统计，沿用 `inc_day` 分区
- **退款状态过滤**：仅统计退款成功的订单（`refund_status = '2'`）
- **分区过滤**：必须使用 `inc_day` 分区字段
- **去重逻辑**：退款订单数按 `order_id` 去重
- **向后兼容**：既有 order_count / customer_count / gmv / avg_order_amount 四个指标的口径与实现不得改动

## 目标表变更

- **表名**：`dw_demo.dws_order_daily_stat_di`（存量表，非新建）
- **变更类型**：新增字段（refund_order_count、refund_amount）
- **历史分区**：当日之前 30 天分区补跑退款指标，更早分区保持默认值

## 交付要求

1. 存量表变更 DDL（新增字段语句）
2. ETL SQL 开发（既有逻辑保留 + 退款指标合入）
3. 数据质量测试用例（退款指标的唯一性、完整性校验；既有指标回归校验）
4. 字段血缘图（含新增退款来源）
5. 交付报告

## 验收标准

- SQL 通过代码审查（无 SELECT *、有分区过滤、JOIN 带有效条件）
- 既有四个指标的取数逻辑未被改动
- 退款指标有对应的 DQC 测试用例
- 产出完整交付物
