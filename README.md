# Data-Copilot (Data Engineering Automation Agent)

[![Python Tests](https://github.com/JohnnyQ-commits/Data-agent/actions/workflows/python-tests.yml/badge.svg)](https://github.com/JohnnyQ-commits/Data-agent/actions)
![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Build](https://img.shields.io/badge/build-passing-brightgreen)

`Data-Copilot` 是一款工业级数据仓库开发自动化 Agent，覆盖从需求理解、架构设计、代码生成、风险预估到质量闭环的完整数据工程生命周期。

## 🌟 核心特性 (Core Features)

- **Agentic Workflow**: 基于 Phase 1-6 的确定性工作流，支持需求自动澄清与自愈式开发。
- **DQC 闭环 (Data Quality Control)**:
    - **普适性校验**: 自动覆盖唯一性、时效性、引用一致性及波动监控。
    - **量化评分**: 基于权重（High/Medium/Low）生成健康得分（0-100）。
    - **故障自愈**: 根据 DQC 报告自动推导修复建议并执行 SQL 闭环修复。
- **自动化血缘 (Auto Lineage)**:
    - **字段级追踪**: 深度解析 SQL 映射关系，生成 Mermaid 格式的可视化血缘拓扑。
    - **文档实时同步**: 血缘图谱自动嵌入设计文档，确保代码与架构实时一致。
- **风险预估 (Cost Estimation)**:
    - 静态扫描 SQL 逻辑，预估查询成本与数据偏斜风险，拦截高危操作。
- **提效看板 (Productivity Dashboard)**:
    - 实时统计 Agent 节省的工时、代码行数及自动化率，量化数字化身价值。
- **多业务域语义层 (Headless BI)**:
    - 采用分域存储（JSON）与自动化文档聚合，支持多源数据建模。

## 🚀 工作模式

| 模式 | 适用场景 | 流程 |
| :---: | --- | --- |
| **全量开发 (Full-Cycle)** | 从零开发新需求 | 需求澄清 → 方案设计 → 血缘推导 → SQL生成 → 成本预估 → DQC闭环 → 交付存档 |
| **质量增强 (QC-Boost)** | 存量代码质量提升 | DQC 脚本生成 → 模拟/运行测试 → 质量得分评估 → 自动修复建议 |
| **文档同步 (Doc-Sync)** | 架构调整与知识维护 | 设计文档变更 → 自动同步 DDL/语义 JSON → 重新生成可视化拓扑 |

## 📂 项目结构

```
Data-Copilot/
├── scripts/
│   ├── check_data_quality.py   # DQC 闭环引擎 (评分+自愈)
│   ├── gen_lineage.py          # 字段级血缘解析器 (Mermaid)
│   ├── analyze_productivity.py # 提效量化看板生成器
│   ├── estimate_cost.py        # SQL 成本预估与风险扫描
│   ├── sync_design.py          # 设计文档-代码双向同步引擎
│   └── ...
├── templates/
│   ├── dqc.sql                 # 普适性数据质量模板
│   ├── report.md               # 工业级交付报告模板
│   └── ...
├── PRODUCTIVITY_REPORT.md      # 实时提效战报
├── PROJECT_EVALUATION.md       # 项目评估报告
└── ...
```

## 🛠️ 快速开始

1. **环境准备**: `pip install -r requirements.txt`
2. **提效分析**: `python scripts/analyze_productivity.py` 查看当前 Agent 贡献。
3. **需求录入**: 将需求描述发送给 Agent，系统将自动进入 Phase 1 阶段。

---
*Powered by Trae IDE & Gemini-3-Flash*
