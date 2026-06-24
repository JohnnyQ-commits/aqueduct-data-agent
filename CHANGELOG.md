# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-06-24

### Added

- **LLM timeout as exception**: `_chat_cli` now raises `LLMTimeoutError` instead of silently converting `subprocess.TimeoutExpired` to `"[LLM timeout]"` string, allowing callers to properly catch and handle timeouts
- **LLM timeout auto-retry**: CLI backend retries with exponential backoff on timeout (max 2 retries, timeout doubles 600s -> 1200s -> 2400s), configurable via `AQUEDUCT_LLM_MAX_RETRIES`
- **Full call_llm logging**: `helpers.call_llm()` logs task_type, model_id, prompt/response size, token usage, and elapsed time before and after each call
- **Phase timing logs**: All 7 Phase nodes now emit "start/complete" logs with elapsed seconds and artifact size
- **SQL validity check**: Added `is_valid_sql()` guard after `extract_sql_block()` — invalid SQL writes a halting error and stops the pipeline
- **Tool execution logging**: ValidatorTool, LineageTool, EstimatorTool `execute()` methods now log input/output with pre-validation
- **Per-task log files**: Pipeline creates `task.YYYY-MM-DD.log` in output directory, logs are synced to both global and task-level handlers
- **ModelRouter routing log**: `route()` method now logs task_type -> model routing decisions
- **Review-fix loop**: Phase 4.5 review parses Critical/Warning issues; if found, builds fix prompt and loops back to Phase 4 (up to `max_fix_iterations`)
- **External SQL input**: `--sql-file` CLI arg and `external_sql_path` config — Phase 4 can skip LLM and read SQL directly from file
- **LLM-based field lineage**: `_auto_lineage()` replaced regex parsing with `call_llm("lineage")` — handles CASE/WHEN, COALESCE, CTE chains; new `lineage.tpl.md` prompt template
- **Claude Code Skills**: `CLAUDE.md` project instructions + `aqueduct-dev` / `aqueduct-review` skill definitions for zero-config pipeline launch
- **New `sql_fix.tpl.md`**: SQL fix prompt template for review-fix loop
- **New `utils/task_logger.py`**: Task-level log file management module
- **New `LLMTimeoutError` exception**: Inherits from `LLMError`
- **tests/test_core.py**: New test module with 10 tests covering core pipeline execution (`_run_pipeline`) and review-fix loop (`_run_fix_loop`)

### Fixed

- **Phase4 empty SQL bug**: Root cause chain `timeout silently swallowed -> invalid SQL saved -> tools validate garbage -> pipeline continues`. Solved with 4-layer defense: timeout raises exception + SQL validity check + tool input pre-validation + pipeline circuit breaker

**Phase 1 — 紧急修复 (6 items)**

- **MemoryError naming conflict**: Renamed `MemoryError` → `KnowledgeRecallError` to avoid shadowing Python built-in `MemoryError`
- **WorkflowHaltError**: Added dedicated exception for pipeline halt conditions, replacing string-based error detection
- **recall.py recall() method**: Fixed incorrect method signature that prevented KnowledgeRecall from being called
- **report.py logic inversion**: Fixed inverted condition that showed "execution disabled" when execution was actually enabled
- **Template safe_substitute**: Replaced `str.replace()` with `string.Template.safe_substitute()` to prevent KeyError on missing placeholders
- **sql.py None guards**: Added None checks before accessing `sql_content` to prevent AttributeError when Phase 4 produces no SQL

**Phase 2 — 重要修复 (9 items)**

- **Unified _PROJECT_ROOT**: Removed duplicate `_PROJECT_ROOT` definitions across 3 files; single source of truth now in `config/settings.py`
- **ModelRouter model tiers**: Haiku/Sonnet/Opus now use distinct model IDs from settings instead of all defaulting to the same model
- **Path truncation fix**: `get_output_dir()` now preserves full subpath instead of extracting only the filename
- **save_artifact path traversal**: Added `Path(filename).name` sanitization to prevent directory traversal in artifact filenames
- **fix_loop iteration protection**: Added `fix_iterations` check against `max_fix_iterations` to prevent infinite review-fix loops
- **context.py add() return**: `add()` now returns `False` when message is truncated away after budget overflow
- **claude.py temp file security**: Changed from predictable `.claude_tmp/` to `tempfile.mkdtemp()` with cleanup
- **dqc.py hardcoded is_success**: Removed hardcoded `is_success=True`; now checks `execution_enabled` from settings
- **Skills import chain**: Replaced fragile sequential imports with `importlib.import_module` in try/except loop per module

**Phase 3 — 改进提升 (fixes)**

- **store.py score normalization**: Capped similarity score at 1.0 via `min(1.0, total_matches / max_possible)`
- **code_review.py dict repr**: Pre-formats `validation_result` dict to readable markdown before passing to LLM template
- **classify_error exception types**: `recovery.py` now checks exception type hierarchy before string keyword fallback
- **Eliminated fabricated data**: Removed hardcoded scan volume from `estimator.py`; replaced hardcoded DQC counts in `productivity.py` with actual state data

### Changed

- `WorkflowState` added `fix_iterations`, `external_sql_path` optional fields
- `Settings` added `max_fix_iterations`, `llm_max_retries`, `external_sql_path` config entries
- `Aqueduct.dev()` added `external_sql_path` parameter
- CLI `dev` command added `--sql-file` parameter
- **MCP protocol compliance**: Added JSON-RPC 2.0 initialize handshake, process caching, and proper `close()` in `mcp/client.py`
- **CI coverage threshold**: Added `--cov-fail-under=50` to CI pipeline
- **WorkflowState sub-types**: Added `PhaseContext`, `PhaseArtifacts`, `ChangeManagementState` TypedDict sub-types for documentation
- **workflow.py ADR**: Added Architecture Decision Record explaining core.py vs workflow.py dual-engine coexistence
- **SECURITY.md**: Updated version table to 0.4.x and contact email to security@aqueduct.dev
- **pyproject.toml**: Version synced from 0.3.1 to 0.4.0

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
