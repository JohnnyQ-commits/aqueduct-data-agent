# 数据开发 Agent

[![Python Tests](https://github.com/JohnnyQ-commits/Data-agent/actions/workflows/python-tests.yml/badge.svg)](https://github.com/JohnnyQ-commits/Data-agent/actions)
![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Build](https://img.shields.io/badge/build-passing-brightgreen)

数据仓库 SQL 开发自动化 Agent，覆盖从需求到上线的完整开发流程。

## 工作模式

| 模式 | 适用场景 | 流程 |
| :---: | --- | --- |
| **开发模式** | 从零开发新需求 | 需求理解 → 设计方案 → 表结构设计 → SQL开发 → 代码审查(可选) → 数据质量测试 → 设计文档 + 知识沉淀 |
| **审查模式** | 已有的变更需要验证 | 代码审查（线上版本 vs 变更版本）→ 数据质量测试 → 设计文档 + 知识沉淀 |

## 功能

- 需求理解与澄清提问（自动识别歧义点，先问再做）
- 设计方案输出与确认
- 表结构 DDL 生成
- 核心 SQL 编写（符合团队代码风格）
- **多业务域语义层**：支持分域存储（`knowledge/domains/`）与自动化文档聚合，兼顾机器执行效率与人工审计直观性。
- **SQL 自动校验**：内置 6 大红线检查，支持正则性能优化与边界场景识别。
- 代码审查（差异比对/需求覆盖验证/下游影响分析）
- 数据质量测试用例生成
- 设计文档自动生成
- 项目交付总报告生成
- 知识沉淀（语义层模型/编码约定/命名规范）
- 批量查表结构（scripts/batch_query_tables.py）

## 快速开始

1. **有需求文档**：直接把文件扔过来，我按 6 个阶段逐步推进
2. **没有现成文档**：复制 `templates/requirement.md`，填空式填写 7 个核心项，然后给我

## 项目结构

```
Data-agent/
├── README.md               # 本文件
├── PROJECT_EVALUATION.md   # 项目全面评估报告与后续规划
├── AGENT.md                # 自动化规范 (Data Development Automation Guidelines)
├── WORKFLOW.md             # 流程图与交付物对照表
├── requirements.txt        # 项目依赖清单
├── .gitignore              # Git 忽略配置
├── .github/
│   └── workflows/
│       └── python-tests.yml # GitHub Actions 自动化测试流水线
├── skills/
│   └── data-developer.md   # 数据开发技能定义 (Trigger/Skip/Workflow)
├── scripts/
│   ├── validate_sql.py     # SQL 自动校验工具 (正则优化版)
│   ├── gen_design.py       # 设计文档自动生成工具
│   ├── batch_query_tables.py # 批量查表结构工具 (支持多数据源)
│   └── gen_semantic_doc.py # 语义层文档转换工具 (JSON -> MD+Mermaid)
├── knowledge/
│   ├── semantic-model.md   # 可视化语义层知识库 (人工审计用)
│   └── domains/            # 业务域分域存储目录 (机器执行用)
│       ├── courier_compliance.json
│       └── event_monitoring.json
├── tests/                  # 自动化测试套件 (Pytest)
│   ├── test_validate_sql.py
│   └── test_gen_design.py
├── templates/              # 需求、设计、DQC、报告等标准化模板
└── docs/
    └── coding-style.md     # SQL 代码风格规范
```

## 适用场景

- Hive/Spark SQL 数仓开发
- T+1 离线数据管道
- 临时表/结果表创建
- 数据质量巡检
- 变更代码审查与验证
