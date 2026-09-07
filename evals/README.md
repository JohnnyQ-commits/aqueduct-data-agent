# evals/ — 评估数据集（Agent 测试阶段 1：最小评估闭环）

> 定位见知识库《Agent测试体系规划》：**模板变更守门 + 定期回归，永不进 per-commit CI**
> ——单次评估 = 1 次真实 LLM 跑（干净路径 ~43 分钟，网关慢日 96 分钟），
> 且网关不稳定会污染结果。

## 结构

- `manifest.json` — 用例清单：需求文档 + 必含产物 + 关键词锚点 + DQC 用例下限
- `cases/` — 评估专用需求文档（迭代场景；全新场景直接引用 `examples/`，单一事实源不复制）
- `runs/` — 每次评估的产物与评分卡（git 忽略；`report-YYYYMMDD.md` 即回归基线归档）

## 用例矩阵

| 用例 | 场景 | 需求 |
|---|---|---|
| ecommerce_greenfield | 全新建表 | ../examples/ecommerce_daily_stat.md |
| ecommerce_refund_iterative | 存量表迭代 | cases/order_refund_iterative.md |

## 运行

```bash
python scripts/run_evals.py                   # 全量（发版前人工触发）
python scripts/run_evals.py --case iterative  # 按名称子串过滤单场景
```

评分七项：产物完整 / 结构契约 / 关键内容 / 规范 linter / DQC 用例 / 真实试跑 / 管道错误。
全部复用 v0.6.0 门禁实现（`src/aqueduct/evals.py` 直接 import，门禁改了评估口径自动跟着改）。
断言只锚定"必含章节/关键词/必过门禁"，不做全文 golden diff——对 LLM 输出太脆
（复用 P0-2「契约锚定模板而非金样本」的教训）。

## 判读注意

- 真实试跑在无 DP_* 配置的环境自动 skip（`execution_enabled is not True` 严格判断，不算失败）
- 同一需求连跑 3 次的波动需先排除网关因素（重试次数 / 5xx / 中止次数）再判定退步
- 模板（`.tpl.md` / skill prompt）有 diff 的改动必须附评估报告，分数不降才可合并（阶段 2 守门）
- 数据集腐化防护：需求过时或网关换模型后，全量重跑建立新基线
