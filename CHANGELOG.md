# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-06-23

### Added

- **LLM 超时异常化**: `_chat_cli` 中 `subprocess.TimeoutExpired` 不再静默转为 `"[LLM 超时]"` 字符串，改为抛出 `LLMTimeoutError`，调用方可正确捕获和处理
- **LLM 超时自动重试**: CLI 后端超时后指数退避重试（最多 2 次，timeout 翻倍 600s → 1200s → 2400s），通过 `AQUEDUCT_LLM_MAX_RETRIES` 配置
- **call_llm 完整日志**: `helpers.call_llm()` 在调用前后记录 task_type、model_id、prompt/response 大小、token 用量、耗时
- **Phase 阶段日志**: 7 个 Phase 节点均增加"开始/完成"日志，含耗时秒数和产出物大小
- **SQL 有效性校验**: `extract_sql_block` 后增加 `is_valid_sql()` 兜底校验，无效 SQL 写入错误并终止管道
- **工具执行日志**: ValidatorTool、LineageTool、EstimatorTool 的 `execute()` 方法增加输入/输出日志
- **每任务独立日志**: 管道启动时在输出目录创建 `task.YYYY-MM-DD.log`，日志自动同步写入
- **ModelRouter 路由日志**: `route()` 方法记录 task_type → model 路由决策
- **审查→修复循环**: Phase 4.5 审查发现 Critical/Warning 问题时，自动构建修复 prompt 让 LLM 修复 SQL，回环到 Phase 4 重新执行（最多 `max_fix_iterations` 次）
- **外部 SQL 输入**: `--sql-file` CLI 参数和 `external_sql_path` 配置项，Phase 4 可跳过 LLM 直接读取外部 SQL 文件
- **新增 `sql_fix.tpl.md`**: SQL 修复 prompt 模板
- **新增 `utils/task_logger.py`**: 任务级日志管理模块
- **新增 `LLMTimeoutError` 异常**: 继承自 `LLMError`

### Fixed

- **Phase4 SQL 为空 bug**: 根因链 `超时静默吞掉 → 无效 SQL 保存 → 工具对垃圾内容校验 → 管道继续执行`，通过 4 层防御彻底解决（超时抛异常 + SQL 有效性校验 + 工具输入预检 + 管道熔断）

### Changed

- `WorkflowState` 新增 `fix_iterations`、`external_sql_path` 可选字段
- `Settings` 新增 `max_fix_iterations`、`llm_max_retries`、`external_sql_path` 配置项
- `Aqueduct.dev()` 新增 `external_sql_path` 参数
- CLI `dev` 命令新增 `--sql-file` 参数

## [0.3.1] - 2026-06-17

### Fixed

- **ClaudeLLM env var loading**: Moved model ID defaults from class attributes to instance `__init__`, ensuring `.env` values are correctly picked up after pydantic-settings loads them
- **CLI command injection**: Replaced `shell=True` + string concatenation in `_chat_cli` with list-form `subprocess.run` and file handle redirection
- **Workflow execution duplication**: Unified dev/change pipeline execution into `core._run_pipeline()`, CLI now delegates to `Aqueduct` class instead of reimplementing the node loop
- **Topological sort**: Replaced ad-hoc queue with proper Kahn's algorithm using `collections.deque`; added cycle detection to prevent infinite loops
- **WorkflowState typing**: Replaced `total=False` (all optional) with explicit `NotRequired` for optional fields; required fields (`requirement`, `mode`, `errors`, `artifacts`) are now enforced by type checkers
- **CLI token stats**: Added `estimated` flag to `LLMUsage` so downstream consumers (e.g. productivity board) can distinguish CLI-estimated counts from real API values
- **Keyword extraction**: Filtered Chinese stopwords and switched from single-char to bigram-based extraction for better precision in domain matching
- **Issue class**: Replaced meaningless `class Issue(dict)` with a proper `TypedDict`

### Added

- **DQC execution capability**: Phase 5 now auto-executes DQC test SQL via `SQLExecutorTool` (registered as `executor`). Wraps `HiveExecuteTool` with `health_check()`, `execute()`, and `execute_batch()`. Execution failure does not block workflow — marked as WARN with `Phase5-DQC执行报告.md` output. Controlled by `AQUEDUCT_EXECUTION_ENABLED` env var (default: true, auto-skip if DP_* not configured)
- **KnowledgeRecall wired into CLI workflow**: `node_requirement` now calls `KnowledgeRecall.recall()` at the start of Phase 1, populating `state["domain_context"]` from ontology knowledge base. Previously this was dead code — all 6 downstream nodes read empty strings. Now the full pipeline (dev / review / change) automatically loads matching business domain context
- **Validator tests**: 30+ unit tests covering all 7 SQL validation rules + integration tests
- **Lineage tests**: 10+ unit tests covering table/field lineage parsing and Mermaid generation
- **Workflow tests**: Added execution tests for linear DAG, state propagation, halt on fatal error, and cycle detection
- **Knowledge recall integration tests**: 4 tests verifying domain_context is populated on match, empty on no-match, and readable by downstream nodes

### Removed

- **Unused `templates/` directory**: Removed `templates/` and its `templates_dir` config entry — files were never loaded by any runtime code path

## [0.3.0] - 2026-06-14

Initial public release.

### Added

- **7-layer architecture**: MCP / LLM / Tools / Skills / Engine / Memory / Config
- **CLI with 3 workflow modes**:
  - `aqueduct dev` — full pipeline from requirement to delivery (7 phases)
  - `aqueduct review` — validate SQL changes against online version
  - `aqueduct change` — post-delivery change management with CR tracking
- **9 atomic tools**: SQL validator, cost estimator, field lineage, batch query, design doc generator, DQC engine, semantic doc generator, design sync, productivity board
- **7 core skills**: requirement clarify, design scheme, DDL generate, SQL develop, code review, DQC quality, report delivery
- **Ontology knowledge base**: business domains modeled as typed JSON (entities, relationships, metrics, axioms)
- **3 example domains**: e-commerce orders, SaaS user activity, supply chain inventory
- **Interactive workflow**: node-by-node execution with real-time progress and user confirmation
- **11 mandatory deliverables**: standardized output (DDL, SQL, DQC, Design, Reports, etc.)
- **Prompt-code decoupled**: `.tpl.md` prompt templates, editable without code changes
- **MCP integration**: platform-agnostic data platform connection via standard protocol
- **LLM 3-tier routing**: Haiku (fast analysis) / Sonnet (balanced) / Opus (complex generation)
- **Top-K semantic recall**: auto-load relevant domain knowledge from ontology
- **SQL validation**: 6+1 rules (SELECT *, partition filter, keyword case, divide-by-zero, JOIN ON, aggregate NVL, semicolons)
- **Data quality testing**: 5 categories (uniqueness, business contradiction, consistency, boundary, volatility)
- **Field-level lineage**: auto-generated Mermaid ER diagrams from SQL
- **Cost estimation**: static analysis for Cartesian products, missing partitions, large table joins
- **Error recovery**: DAG state checkpointing and resume capability
- **Claude Code skills**: 3 skill definitions for AI-assisted data development
- **CI/CD**: GitHub Actions with Python 3.10/3.11/3.12 matrix, ruff lint, pytest coverage
- **Full documentation**: README (bilingual), ARCHITECTURE.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md
