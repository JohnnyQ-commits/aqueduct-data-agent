# Data-Copilot (Data Engineering Automation Agent)

[![Python Tests](https://github.com/JohnnyQ-commits/Data-agent/actions/workflows/python-tests.yml/badge.svg)](https://github.com/JohnnyQ-commits/Data-agent/actions)
![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Build](https://img.shields.io/badge/build-passing-brightgreen)

`Data-Copilot` 是一款工业级数据仓库开发自动化 Agent，覆盖从需求到上线的完整开发流程。

## 🚀 工作模式

| 模式 | 适用场景 | 流程 |
| :---: | --- | --- |
| **开发模式** | 从零开发新需求 | 需求理解 → 设计方案 → 表结构设计 → SQL开发 → 代码审查(可选) → 数据质量测试 → 设计文档 + 知识沉淀 |
| **审查模式** | 已有的变更需要验证 | 需求理解 → 代码审查（线上版本 vs 变更版本）→ 数据质量测试 → 设计文档 + 知识沉淀 |

## 🌟 核心功能

- **需求理解与澄清**：自动识别歧义点，先问再做，支持语义层对齐。
- **设计方案输出**：自动生成取数逻辑、字段映射与上下游依赖。
- **表结构 DDL 生成**：支持分区设计与规范化字段定义。
- **核心 SQL 编写**：支持 CTE (With...as) 模式、子查询及数仓分层设计。
- **[今日增强] 自动化血缘 (Auto Lineage)**：深度解析 SQL，生成字段级可视化血缘拓扑。
- **[今日增强] 资源成本预估 (Cost Estimation)**：静态扫描 SQL 逻辑，拦截高危笛卡尔积与大查询。
- **SQL 自动校验 (Smart Fix)**：内置 6 大红线检查，支持 AI 引导的代码自愈修复。
- **[今日增强] 数据质量闭环 (DQC)**：生成普适性测试用例，支持量化评分与健康仪表盘。
- **代码审查 (Code Review)**：支持迭代变更验证，确保 PRD 变更点在代码中 100% 落地。
- **提效量化看板**：实时统计 Agent 节省的工时、代码行数，量化数字化身价值。

## 📂 项目结构

```
Data-Copilot/
├── README.md               # 本文件
├── PROJECT_EVALUATION.md   # 项目全面评估报告与后续规划
├── WORKFLOW.md             # 详细步骤规范与交付物对照表
├── AGENT.md                # 自动化规范 (Data Development Automation Guidelines)
├── PRODUCTIVITY_REPORT.md      # 实时提效战报
├── scripts/
│   ├── check_data_quality.py   # DQC 闭环引擎 (评分+自愈)
│   ├── gen_lineage.py          # 字段级血缘解析器 (Mermaid)
│   ├── analyze_productivity.py # 提效量化看板生成器
│   ├── validate_sql.py         # SQL 自动校验与 Smart Fix
│   ├── estimate_cost.py        # SQL 成本预估与风险扫描
│   └── ...
├── knowledge/
│   ├── semantic-model.md       # 可视化语义层知识库
│   └── domains/                # 业务域分域存储 (JSON)
├── tests/                      # 自动化测试套件 (Pytest)
├── templates/                  # 需求、设计、DQC、报告等标准化模板
└── docs/                       # SQL 代码风格规范等
```

## 🛠️ 快速开始

1. **有需求文档**：直接把文件扔过来，我按 6 个阶段逐步推进。
2. **提效分析**：运行 `python scripts/analyze_productivity.py` 查看当前 Agent 贡献。
3. **环境准备**：`pip install -r requirements.txt`。

---
*Powered by Trae IDE & Gemini-3-Flash*
