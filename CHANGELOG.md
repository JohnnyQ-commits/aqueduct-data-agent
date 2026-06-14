# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
