# 需求：供应链库存周转分析报表

## 背景

供应链团队需要监控各仓库的库存周转情况，识别滞销 SKU 和缺货风险，优化库存管理。

## 需求描述

基于库存快照和出入库流水数据，开发一张库存周转分析表，包含以下指标：

1. **统计日期** (inc_day)
2. **仓库ID** (warehouse_id)
3. **SKU ID** (sku_id)
4. **库存数量** (stock_quantity) — 期末库存
5. **可用库存** (available_quantity) — 库存数量 - 锁定数量
6. **当日出库量** (daily_outbound) — 当日出库流水汇总
7. **库存周转天数** (turnover_days) — 库存数量 / 近 7 天日均出库量
8. **缺货标识** (stockout_flag) — 可用库存 < 安全库存时为 1
9. **补货预警** (reorder_flag) — 可用库存 < 补货点时为 1

## 数据来源

- 库存快照表：`dw_demo.dwd_inventory_snapshot_di`
- 库存流水表：`dw_demo.dwd_stock_movement_di`
- SKU 维度表：`dw_demo.dim_sku_info_df`

## 统计口径

- **统计周期**：按天统计
- **库存周转天数**：`stock_quantity / (近 7 天日均出库量)`，若近 7 天无出库则置为 NULL
- **缺货判断**：`available_quantity < safety_stock`
- **补货预警**：`available_quantity < reorder_point`
- **分区过滤**：必须使用 `inc_day` 分区字段
- **流水类型**：仅统计出库流水（`movement_type = 'OUTBOUND'`）

## 目标表设计

- **表名**：`dw_demo.dws_inventory_turnover_di`
- **分区字段**：`inc_day` (STRING)
- **存储格式**：ORC
- **生命周期**：保留 180 天

## 特殊要求

1. **周转天数计算**：使用滑动窗口计算近 7 天日均出库量
2. **除零处理**：日均出库量为 0 时，周转天数置为 NULL
3. **安全库存和补货点**：从 SKU 维度表获取

## 交付要求

1. DDL 建表语句
2. ETL SQL 开发
3. 数据质量测试用例（含边界值测试）
4. 字段血缘图
5. 交付报告

## 验收标准

- SQL 通过代码审查
- 周转天数计算逻辑正确
- 缺货和补货预警标识准确
- DQC 测试覆盖关键场景
- 产出完整交付物
