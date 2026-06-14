# Aqueduct 架构参考

## 分层架构

```
┌─────────────────────────────────────────────────────────┐
│                      CLI (main.py)                       │
├─────────────────────────────────────────────────────────┤
│                    Engine (DAG 编排)                      │
│  StateGraph → CompiledWorkflow → Node Execution          │
├──────────┬──────────┬──────────┬────────────────────────┤
│  Skills  │  Tools   │   LLM    │       Memory           │
│  7 Skills│  9 Tools │  Router  │  DomainModel + Recall  │
├──────────┴──────────┴──────────┴────────────────────────┤
│                      MCP (数据平台对接)                    │
├─────────────────────────────────────────────────────────┤
│                    Config (pydantic-settings)             │
└─────────────────────────────────────────────────────────┘
```

## 核心模块

### Engine (`src/aqueduct/engine/`)
- `state.py` — WorkflowState TypedDict
- `workflow.py` — StateGraph + CompiledWorkflow
- `recovery.py` — 错误恢复（指数退避 + jitter）
- `nodes/` — 7 个节点模块 (requirement → report)

### Skills (`src/aqueduct/skills/`)
| Skill | Phase | 职责 |
|-------|-------|------|
| requirement_clarify | 1 | 需求理解与歧义识别 |
| design_scheme | 2 | 设计方案生成 |
| ddl_generate | 3 | DDL 表结构生成 |
| sql_develop | 4 | 核心 SQL 开发 |
| code_review | 4.5 | 代码审查 |
| dqc_quality | 5 | DQC 质量测试 |
| report_delivery | 6 | 交付报告生成 |

### Tools (`src/aqueduct/tools/`)
| Tool | 功能 |
|------|------|
| validator | SQL 规范校验（7 项检查） |
| lineage | 血缘解析 + Mermaid 图 |
| estimator | 成本预估 + 风险评估 |
| dqc | DQC 测试解析 + 质量看板 |
| semantic | 语义模型文档生成 |
| design | 设计文档生成 |
| sync | DDL 同步 |
| batch_query | 批量元数据查询 |
| productivity | 效能统计 |

### LLM (`src/aqueduct/llm/`)
- `claude.py` — ClaudeLLM（SDK + CLI 双后端）
- `router.py` — 三档模型路由（ANALYSIS/MEDIUM/HEAVY）
- `context.py` — Token 预算管理

### Memory (`src/aqueduct/memory/`)
- `domain.py` — DomainModel Pydantic 模型
- `store.py` — MemoryStore（LRU 缓存）
- `recall.py` — KnowledgeRecall（Top-K 召回）

## 工作流模式

### 开发模式 (dev)
```
requirement → design → ddl → sql → review → dqc → report
   (Phase 1)   (2)    (3)   (4)    (4.5)   (5)    (6)
```

### 审查模式 (review)
```
requirement → review → dqc → report
   (Phase 1)   (4.5)   (5)    (6)
```

## CLI 命令

```bash
# 开发模式
aqueduct dev <requirement.md> [-o output_dir]

# 审查模式
aqueduct review <online.sql> <changed.sql> [-d description]

# SQL 校验
aqueduct validate <sql_file> [--strict]

# 项目状态
aqueduct status

# 详细日志
aqueduct --verbose <command>
```
