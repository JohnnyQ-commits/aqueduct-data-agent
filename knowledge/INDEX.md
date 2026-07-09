# Aqueduct 知识库索引

> **自动更新时间**: 2026-07-09 16:43:18
> **说明**: 本文件为知识库总入口。每个业务域有独立的 `domain.json`（机器执行）和 `semantic-model.md`（人工审计）。

---

## 目录结构

```
E:\test\aqueduct\knowledge/
├── INDEX.md                    # 本文件（总入口）
├── domains/
│   ├── {domain_id}/
│   │   ├── domain.json         # 机器执行（Pydantic 校验）
│   │   └── semantic-model.md   # 单域审计文档
│   └── ...
```

---

## 业务域列表

| 域 ID | 名称 | 描述 | 版本 | 审计文档 |
| :--- | :--- | :--- | :--- | :--- |
| `ecommerce_order` | 电商订单分析 | 示例业务域：电商平台订单全链路分析，覆盖下单、支付、发货、签收等核心环节 | 1.0.0 | [查看](domains/ecommerce_order/semantic-model.md) |
| `saas_user_activity` | SaaS 用户活跃分析 | 示例业务域：SaaS 产品用户活跃度分析，覆盖 DAU/MAU、留存率、功能渗透率等核心指标 | 1.0.0 | [查看](domains/saas_user_activity/semantic-model.md) |
| `supply_chain_inventory` | 供应链库存分析 | 示例业务域：供应链库存周转与补货分析，覆盖库存水位、周转天数、缺货预警等核心场景 | 1.0.0 | [查看](domains/supply_chain_inventory/semantic-model.md) |

---

**共 3 个业务域**
