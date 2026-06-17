# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
