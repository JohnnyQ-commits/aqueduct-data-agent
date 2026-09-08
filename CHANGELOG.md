# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Pipeline checkpoint & resume (P1-3 / PERF-6, 断点续跑)**: an interrupted pipeline run previously lost everything — v5's Phase 5 failure meant the first four phases' 108 minutes of work was discarded and the only option was a full rerun. Every phase that genuinely completes now writes a serializable state snapshot into the existing `.pipeline_manifest.json` (`phases_completed` prefix + `state_snapshot`, via a new `on_phase_complete` hook in `_run_pipeline`; runtime objects — table-schema cache, lineage/DQC executors and futures — are excluded from the snapshot, while serializable private state like `_review_issues`/`fix_iterations` is kept so the fix-loop trajectory survives; completed phases' degradation errors are restored too, keeping the eval trajectory intact). The hook fires only where "completed" is real: halted phases never checkpoint, a review phase mid fix-loop doesn't checkpoint until the loop converges (back-jump re-review included), a failing checkpoint callback only logs (断点续跑 must not become a new failure mode), and a user stopping at the interactive confirm still checkpoints the finished phase. `Aqueduct.dev(..., resume=True)` / `aqueduct dev --resume` loads the checkpoint (requirement-hash matched), restores the snapshot (current-run metadata keys win on conflict, so resuming into a different output dir can't regress), validates `phases_completed` as a true prefix of the current phase list (corrupt/foreign manifests degrade to a full run — same for missing checkpoints and changed requirements), and executes only the remaining phases; resuming an already-complete run is a no-op returning the last result. OPT-7 stays compatible: `save_checkpoint` derives `phase1_outputs` from the snapshot, so incremental Phase 1 skipping keeps working off the same file. 30 new tests (`tests/test_resume.py`, including pipeline-level resume integration on fake phases); suite at 609.

- **Scorecard gateway-health annotation (phase-2 轨迹指标 pulled forward)**: the first real eval run proved the need — the scorecard caught the iterative case's FAIL but couldn't self-attribute it to the LLM gateway (`ConnectionRefused` mid-run produced a 55-char degenerate response that the structure gate correctly degraded). `RunHealth` now extracts the gateway fingerprint from each run's `task.*.log` (LLM timeouts, LLM retries, platform 302/connection failures, degradation events; MCP table-not-found deliberately excluded as a dataset trait, not a runtime anomaly) and the scorecard renders it as a 网关健康 column plus an automatic ⚠️ pollution warning on any FAIL whose run shows LLM-gateway anomalies — "判定退步先排除网关因素" is now mechanical instead of a manual log-reading step. The re-rendered first baseline demonstrates it end-to-end: greenfield `平台连接异常2` (two cookie 302s, honestly counted), iterative `LLM超时1·重试1·平台连接异常2·降级2` with the warning attached. 6 new tests using the real log lines as fixtures (31 in `test_evals.py`); summary rows also gain 管道遗留错误 detail lines.

### Fixed

- **Eval trial scorer three-state fix (caught by the first real eval run)**: when the data platform is unreachable mid-run (health_check 302 on an expired cookie), `_trial_run_issues` self-skips and returns `[]` — indistinguishable from a genuinely passing trial, so the scorecard recorded "试跑通过" for a trial that never executed. The scorer now distinguishes the states by the signal the real gate already writes (`state["trial_run_result"]`, set only when statements actually execute): issues → fail, result present → pass with tested/passed counts, absent → skip ("平台不可用或 SQL 无效，试跑未执行"); stale pipeline-left results are popped and re-verified live rather than trusted. 3 new tests (25 in `test_evals.py`); the seal now covers the enabled-and-selfskip path the autouse fixture previously masked.

### Added

- **Eval layer — minimal eval loop (Agent-testing phase 1)**: `src/aqueduct/evals.py` turns the v0.6.0 quality gates into an eval harness — an eval run is one `dev` pipeline pass plus the gates re-assembled as scorers (imported, never copied: artifact presence, P0-2 structure contract, keyword anchors, P0-1 SQL linter with Critical-as-veto, P0-3 DQC case count with `[DQC降级]` as instant fail, P1-2 real trial run, pipeline errors), rendered into a Markdown scorecard (`evals/runs/report-YYYYMMDD.md`). Dataset `evals/` ships two desensitized cases covering both scenarios — greenfield (`examples/ecommerce_daily_stat.md`, referenced in place, not copied) and a new synthetic iterative requirement (`cases/order_refund_iterative.md`: refund metrics added to an existing table with backward-compatibility constraints); assertions anchor on required artifacts/keywords/gates, never full-text golden diffs (reusing the P0-2 "contract anchors, not goldens" lesson). Entry point `scripts/run_evals.py` (`--case` substring filter, exit 0/1) — positioned for template-change gating and periodic regression, deliberately never per-commit CI (one eval = one real LLM run, 43–96 min, and gateway instability pollutes scores). A contract test (`TestRealManifest`) guards `manifest.json` against artifact names the pipeline never writes — written after exactly that mistake (`Phase5-质量仪表盘.md` imported from the plugin-mode list) and would have caught it before a wasted 43-minute run; a live smoke against the real v5 output confirmed the scorer discriminates (v5's pre-P1-1 Phase3 rehearsal SQL correctly fails the DDL keyword anchor, its real Phase4 SQL passes the linter 0/0). 22 new sealed tests (autouse fixture seals the trial gate — local `.env` credentials otherwise leak `execution_enabled=True` into unit tests and reach the real platform), suite at 570.

## [0.6.0] - 2026-09-07

### Added

- **Trial run is now a mandatory gate (P1-2) + the executor was never actually reachable**: two stacked defects made `_auto_trial_run` a no-op on every real pipeline run. First, `_extract_select_statements` only accepted statements *starting* with SELECT/WITH — real ETL SQL is a single `INSERT OVERWRITE ... PARTITION (...) SELECT ...` statement, so the extractor returned 0 statements and the trial was silently skipped 100% of the time; it now strips the INSERT head (first word-boundary `select`/`with`, covering both the multi-line v5 form and one-liners, with INSERT-VALUES statements excluded so string literals containing 'select'/'with' can't be mis-stripped into garbage). Second, the platform adapter's endpoints were dead paths — `/data-platform-api/hive/*` returns 404; corrected to `/bdp-fc-ide-external-controller/hive/*` (the path data-agent and ai-sql-generate both use; verified live: the endpoint answers, currently with a CAS 302 because the `.env` cookie is expired — refresh `DP_COOKIE` to re-enable execution). The gate itself moved to the review side: `_trial_run_issues` runs on every review pass, executes each SELECT body with `LIMIT 10` appended (existing LIMIT not duplicated — the old `split()[-1:]` check appended a second LIMIT to SQL ending in `limit 10`) via the shared `_run_trial_selects`, and injects failures as `Critical` issues into the fix loop; back-jumps re-check the fixed SQL live (same structure as the P0-1 linter), and failures persisting past `max_fix_iterations` halt the pipeline. Zero-false-positive guard chain: SQL invalid/short → skip, `execution_enabled is not True` (strict — MagicMock attributes from partially-mocked test settings auto-skip, preventing real platform connections in unit tests) → skip, `health_check` fails or raises → skip (a connection problem is never reported as a SQL problem). Phase 4's `_auto_trial_run` keeps its report-writing role and now goes through the same `_run_trial_selects`; a smoke script (`scripts/smoke_trial_gate.py`) replays a real v5 deliverable through the full chain (extractor verified live: 1 statement extracted vs. 0 before; the platform-skip path verified live via the expired-cookie 302). 18 new tests (the old `test_skips_insert` asserted the extractor defect itself and was updated), suite at 548

- **Phase 1 split into two parallel small calls — design+summary ∥ DDL (P1-1, second spiral magnet defused)**: the OPT-5 three-in-one call (requirement summary + design scheme + DDL in one response) was the pipeline's second spiral magnet after dqc_gen — measured completion 21652 tokens against the 24000 abort threshold, a ~2000-token margin, and the always-thinking production model thinks deeper the more tasks one prompt stacks. `node_requirement` now runs A (`design_ddl`, requirement summary + design scheme via the trimmed `requirement_and_design.tpl.md` — DDL task, DDL rules, DDL output part and DDL prohibitions removed) in parallel with B (`ddl_gen`, a new `ddl_generate_req.tpl.md` that generates the target-table DDL straight from the requirement document + table schemas + target table, not from the design scheme; no reasoning-step checklist, per the P0-3 lesson) through a `ThreadPoolExecutor` — B's inputs are the same sources A reads, so the split costs no serial latency (wall-clock = max(A, B)). Consistency between the two independent outputs is guarded by a deterministic field-set comparison in code: `_extract_mapping_fields` parses the design scheme's 字段映射 table (tolerates the type-annotated first column real output produces, e.g. `order_count (bigint)`), `_extract_ddl_fields` parses CREATE TABLE column definitions including `PARTITIONED BY` (both keyed on type keywords to avoid matching COMMENT text), and a mapping field missing from the DDL triggers one targeted DDL re-generation with the missing fields appended; still inconsistent → the DDL is discarded and the existing serial Phase 3 node takes over (`ddl_content` stays empty — the same fallback covers B exceptions, missing SQL blocks and template loss, each recorded in `errors` as a fallback event, never killing the pipeline); either side unparseable → the check is skipped (zero-false-positive principle). The P0-2 structure gate still applies to A's products only (A regeneration never re-rolls B), and `_split_requirement_and_design` is unchanged — it already returns an empty DDL for SQL-block-free responses, so all existing callers keep working. Real-LLM smoke on the standard example (with live MCP schemas for 2 of 3 source tables): A completion 3330 tokens / 210.9s, B 285 tokens / 33.3s (vs. 21652 combined before — the margin to the 24000 threshold went from ~2000 to ~20000), zero gate retries, zero consistency fixes, mapping and DDL field sets fully aligned, wall-clock 219.2s ≈ max(A, B); 18 new tests, suite at 529

- **Artifact structure contract gate (P0-2, missing-chapter protection)**: LLM outputs previously landed on disk unvalidated — a response that skipped the template's required sections was saved as-is (and v5 exposed a worse failure: the `report_delivery` template instructed `doc_gen` to generate three documents in one call, so the entire three-doc response — including a delivery report and a knowledge doc that each have their own generation path — was written verbatim into `Phase6-Design.md`). New `engine/contract.py` defines per-artifact section contracts aligned with each prompt template's output-format instructions (template-required sections = contract-checked sections; contracts are anchored to templates, not to golden samples, whose wording variants would false-positive) for the four LLM-authored documents — `Phase1-需求理解摘要.md` (target-table list + 待确认问题), `Phase2-设计方案.md` (取数逻辑/字段映射/上下游依赖, both the OPT-5 H3 and OPT-4 H2 template styles), `Phase6-Design.md` (需求背景/设计方案/表结构/核心SQL/血缘图 — a mermaid block also satisfies lineage), `Phase6-知识沉淀.md` (title + the 5-chapter knowledge structure); SQL artifacts stay with `is_valid_sql` + the P0-1 linter. Gate flow per document: validate → on missing sections, one targeted regeneration with a hint appended to the prompt (shared `build_retry_prompt`) → still missing → save with a `> ⚠️ 结构门禁告警` banner plus an `errors` entry (degrade, never kill the pipeline — same pattern as the P0-3 DQC degradation). Multi-artifact responses (the Phase 1 three-in-one and the Phase 2 two-in-one) go through `gate_response`, which re-splits the regenerated response and banners only the files that are still incomplete; the LLM call is injected as a callback so `contract.py` stays free of LLM-layer dependencies. `node_report`'s knowledge doc validates inside its worker thread and reports via banner scanning (`scan_degradation`) on the main thread. The `report_delivery.tpl.md` template is fixed to generate only `Phase6-Design.md` (with an explicit required-section skeleton matching the contract); the delivery report and knowledge doc are generated by their own paths. Regression on v5's actual outputs: zero false positives — all four documents pass (heading-numbered sections like `## 4. 表结构（DDL）` match via the prefix-tolerant regexes), while constructed missing-section samples are correctly intercepted; 27 new tests, suite at 511

- **DQC generation split into 5 per-category calls (P0-3, spiral root fix)**: `dqc_gen` previously sent one large prompt covering all 5 test categories (uniqueness / business refutation / cross-table consistency / boundary / fluctuation) under a strict comment format plus business-refutation rules — constraint density so high that the always-thinking production model spiraled deterministically (v5 measured: 9/9 calls burned all 32768 tokens with zero text; retries never recovered, since a spiral re-roll on the same prompt re-spiraled). Phase 5 now generates each category in its own small call — new per-category template `dqc_quality_category.tpl.md` with only that category's instructions (and the 7-step "reason internally, don't output" checklist dropped, itself a spiral trigger for always-thinking models), category definitions centralized in `_DQC_CATEGORIES` — runs the 5 calls in parallel (`ThreadPoolExecutor`), and merges them in fixed category order into the same `Phase5-数据质量测试.sql` artifact, so `_parse_dqc_sql` and DQC execution are unchanged. The speculative executor (PERF-9) submits the split as a single future, keeping the hash-guard reuse path intact. Failure isolation: a category response is only accepted when it carries at least one `-- [...]` case header (the `_parse_dqc_sql` contract — a real-LLM smoke run caught a 55-char canned gateway error being silently merged as "success" during congestion; such responses now get exactly one retry, then degrade with a `[DQC降级]` comment plus an `errors` entry); only all-5 failure raises, matching the fix-loop degradation pattern. `task_type` stays `dqc_gen`, so model routing and timing logs are unchanged. Smoke validation on the exact v5 spiral inputs: 3 consecutive rounds × 5 parallel categories, all clean — 20 cases per round, zero degradation, per-call completion 789–1435 tokens (vs. 32768 zero-text burn before), wall-clock 318–625s per round (slowest category, absorbed by the PERF-9 speculative window in the real pipeline)

- **Deterministic SQL-standards linter wired into the review fix loop (P0-1)**: the `Validator` tool gained 4 new ERROR-level rules (forbidden CTE per §6.3, forbidden partition columns `cur_date`/`data_date`/`riqi` per §1.1, forbidden CROSS JOIN per §10.6, tmp-table database must carry a `tmp_` prefix per §1.3) plus a `content=` mode that validates in-memory SQL, and `node_review` now runs it on every review pass — ERROR violations enter the review issues as `Critical` and trigger the fix loop at zero token cost; each loop back-jump re-checks the fixed SQL automatically. Rules were calibrated against 17 real golden-sample deliverables (three production directories): the division check now strips string literals and inline comments before matching and whitelists `nullif`/`nvl`/`coalesce`/`if`/`case`/`count` denominators (48 legal `count(distinct inc_day)` weekday-average denominators would otherwise false-positive), the tmp-table rule accepts `tmp_` + business-db naming (the standards doc's `tmp_demo` is demo-env wording; real deliveries use e.g. `tmp_dm_tc_waybillinfo.tmp_xxx`) with DDL `if not exists`/`external` exempt, `check_nvl` was downgraded to INFO (bare `sum(field)` is legal in real deliveries), and `check_keyword_case` upgraded to ERROR. Golden-sample regression: zero false positives — the two remaining alarms are verified true positives (101 uppercase-keyword violations in a pre-standards-v1.0 delivery, and one unprotected division the corrected rerun of the same table later fixed with `/ nullif(coalesce(...), 0)`)

- **Real source-table schemas now reach Phase 4 (SQL generation)**: `table_schemas` (live MCP metadata queried in Phase 1) was previously injected only into the Phase 1 prompt — Phase 4 saw source columns only through two layers of paraphrase (Phase 1 summary → Phase 2 design). `node_sql` now passes `table_schemas` into the `sql_develop` skill (with the same inp-or-state fallback pattern), the prompt template renders it as the authoritative source-column list, and the template's rules now state that field names in the design scheme conflicting with the real schema are treated as transcription errors (with a graceful "未获取 / schemas unverified" note when MCP is not configured)

- **Historical deliverable retrieval (`aqueduct search-history` + `memory/history.py`)**: Phase 1 of the data-developer flow now mandates a retrieval pass over past `output/` deliverables and `knowledge/` before requirement understanding — per-table insert statements (target table + partition expression), knowledge-doc hit lines and semantic-model hits, plus a "not distilled" hint for tables delivered without a `knowledge/domains/*.json` (fixes wrong output-rhythm inference on tables that were delivered but never distilled). The module is pure stdlib and runs venv-free: `python src/aqueduct/memory/history.py --doc <req.md>`
- **Data verification checklist (`references/verification_checklist.md`) + Phase 5 two-layer restructure**: Phase 5 of the data-developer flow is now a mandatory pre-launch gate (tmp-table live verification V1~V7 with dual-scenario baselines — iterative vs greenfield: production-table regression vs driver-table/pk-first-verify, non-deterministic-field set comparison, DDL alignment, sample manual checks) followed by post-launch DQC monitoring cases; DQC cases must themselves be executed on the tmp table before delivery. Includes a platform-pitfall checklist (single-statement IDE, `$[0]` bash expansion, JSON path variants, metadata sync delay, permission identity gaps, GBK console)

### Changed

- **Streaming thinking-spiral early abort (`AQUEDUCT_LLM_SPIRAL_ABORT_TOKENS`, PERF-10)**: the SDK backend now consumes the raw event stream (instead of the `text_stream` aggregate, which hides thinking events) and watches two burn signals — cumulative `output_tokens` from `message_delta` events (authoritative, but the production gateway only delivers usage at stream end, as a measured run showed the abort firing at 32768) and a local character-based estimate of accumulated `thinking_delta` text (always available mid-stream) — when either exceeds the threshold (default 24000) with zero text produced, the call is aborted and returns an empty response that flows into the existing empty-response retry. Evidence: the gateway ignores `thinking.budget_tokens` (a doomed `sql_gen` call burned all 32768 tokens with zero text over 1014s), and spirals are nondeterministic, so a retry re-rolls the dice. The threshold was recalibrated 12000→24000 from a controlled pair of full-pipeline runs: healthy deep thinking spans 3600~22000 tokens (design_ddl ~17000 / sql_gen ~19000 / sql_review ~22000, all succeeding at 24000 after twelve consecutive kills at 12000), while thinking beyond ~24000 leaves no room for text inside `max_tokens=32768` and is structurally doomed. This cuts a doomed ~1000s call to ~threshold/32tok/s. Set 0 to disable

- **Speculative DQC generation parallel to review (PERF-9)**: `node_review` now starts the DQC prompt's LLM call (`dqc_gen`) in a background thread as it enters Phase 4.5 — DQC's inputs (`ddl_content`/`sql_content`/`domain_context`) do not depend on the review result, so the only serial-order reason was the review-fix loop possibly rewriting the SQL. `node_dqc` consumes the speculative result only if an `(sql, ddl)` input hash still matches (stale results from a fix-loop rewrite are discarded and regenerated); restarts during the fix loop shut down the previous speculative executor. Same state-stashed-future pattern as the async lineage call; short/invalid SQL skips speculation entirely
- **Phase 6 LLM calls now run in parallel (PERF-3)**: `doc_gen` and `knowledge_extract` take independent inputs (both read from `WorkflowState`), so `node_report` runs them concurrently via a `ThreadPoolExecutor` (same pattern as the Phase 4 async lineage call) — Phase 6 wall-clock becomes `max(two calls)` instead of their sum; artifact save order and error handling are unchanged
- **`AQUEDUCT_LLM_BACKEND` backend override (PERF-1)**: new `llm_backend` setting (`auto`/`sdk`/`cli`/`claude-cli`) forces the LLM backend, overriding auto-detection — previously any machine with the `claude` CLI installed always used the subprocess backend (cold start per call, no prompt caching, estimated tokens). The SDK client now sends the token as both `Authorization: Bearer` and `x-api-key` (same value) so self-hosted gateways that only accept Bearer auth work with direct SDK streaming calls
- **SDK thinking budget (`AQUEDUCT_LLM_THINKING_BUDGET_TOKENS`, PERF-7)**: the SDK backend can now cap reasoning tokens for always-thinking models (e.g. glm-5.3) — measured 4.4s vs 17.7s for the same task with a 1024-token budget, and it prevents thinking from consuming the entire `max_tokens` budget and returning an empty text body (root cause of an observed empty-response retry). Defaults to 0 (parameter not sent) for compatibility with non-thinking models on the official API
- **CLI backend timeout no longer doubles on retry (PERF-2)**: timeout retries now keep the configured `llm_timeout_seconds` with exponential backoff (1s/2s), matching the SDK path — the old doubling (900→1800→3600s) inflated worst-case wall-clock per call to 6300s and was the dominant cost (68%) of a measured 39.3-min pipeline run
- **Phase 4 now receives the original requirement document** (CLI quality fix, root cause 1): `node_sql` passes `requirement_doc` into the `sql_develop` Skill, and the prompt template renders it alongside the summary — SQL generation is no longer limited to the lossy Phase 1 summary (field semantics, boundary conditions and implicit constraints are preserved)
- **`requirement_parse` routed to the Sonnet tier** (root cause 2): requirement parsing no longer uses the weakest model tier; the change-management flow benefits, and the main pipeline Phase 1 already routes via `design_ddl` (Sonnet) since the OPT-5 three-in-one merge

### Fixed

- **Fix-loop LLM failure no longer kills the pipeline**: `_run_fix_loop`'s `sql_fix` call was unguarded — when the call exhausted its empty-response retries (e.g. a thinking spiral), `LLMEmptyResponseError` propagated up and crashed the whole run, losing Phase 6 reports and the run summary even though earlier phases had succeeded (observed live: a 92-minute run died this way). The loop now degrades the same way the phase nodes do: the error is recorded in `errors`, the original unfixed SQL is kept, the loop flag is cleared and the pipeline continues
- **Task log now survives a missing output directory**: `setup_task_logging` creates the output directory before attaching the `FileHandler` — previously the handler failed at pipeline start (directory is only created when the first artifact is saved), so API-driven runs (`Aqueduct().dev(...)`) produced no per-call task log for the entire run, losing per-phase timing/token records needed for performance analysis

## [0.5.0] - 2026-08-27

### Added

- **`aqueduct knowledge sync` command**: rebuilds knowledge base docs (`INDEX.md` + per-domain audit docs) via `SemanticTool`
- **Business domain schema compatibility**: `memory/domain.py` normalizes both new Skill-generated JSON (`domain_name` / `table` / list `primary_key` / `formula` / `type`) and legacy demo JSON (`name` / `expression` / `cardinality`) into the internal `DomainModel`; dict/list mixed forms supported
- **Knowledge base dual-directory architecture + Embedder vectorization module**: semantic layer writes only to `internal/knowledge`
- **Pipeline acceleration + semantic layer write scope**: optimization pass over the pipeline; semantic layer no longer pollutes the user-facing knowledge directory
- **data-developer Skill v0.4.0**: new Phase 2 stop point — data architecture confirmation with 6 default decision points (target grain / layering / update strategy / source tables / data-eng-vs-backend boundary / scheduling dependency); architecture-level changes require one extra confirmation round; `sql_standards.md` (12 sections) now tracked in git

### Changed

- **CTE policy unified — fully banned**: all SQL standards docs consistently forbid `WITH` clauses; derived tables or TMP tables required instead
- **Docs count sync**: 10 atomic tools / 9 core skills / 8 nodes / 5 Claude Code skills / 15 deliverables / 373 tests (was 203 at 0.4.2) across CLAUDE.md, README.md, ARCHITECTURE.md

## [0.4.2] - 2026-07-09

### Added

- **Phase 1 Q&A clarification recording**: After Phase 1 outputs the requirement summary, users can answer clarification questions one by one. Answers are appended to `Phase1-需求理解摘要.md` and synced to `state["requirement_summary"]` for downstream phases. New functions: `_parse_questions()`, `_collect_qa()`, `_format_qa_section()`, `_append_qa_to_file()` in `cli/main.py`
- **Idempotent protection**: `_parse_questions()` skips if `### 用户澄清` already exists, preventing duplicate append on re-run

### Fixed

- **`ecommerce_staff_task.json` schema alignment**: Fixed 6 field mismatches with `DomainModel` Pydantic schema: `domain_name`→`name`, `table`→`source` (8 entities), list `primary_key`→string (2 entities), `type`→`cardinality` (5 relationships), `formula`→`expression` (5 metrics), removed entity-level `name` fields
- **Test suite**: All 203 tests pass (was 197 passed / 6 failed before schema fix)

## [0.4.1] - 2026-06-25

### Fixed

- **Phase 4.5 requirement_desc key alignment**: review node did not pass `requirement_desc`, causing code_review Skill to fall back to full requirement doc (500-3000 chars). Now passes `requirement_summary` (200-800 chars)
- **Phase 5 domain_context key alignment**: dqc_quality Skill read `business_rules`/`domain_axioms` (always empty), but node passed `domain_context`. Unified to `domain_context` so DQC generation can access business rules and semantic model info
- **Phase 1 target_table hardcoded default**: No node wrote `target_table` to state, causing design_ddl Skill to always use hardcoded `dw_demo.tmp_target_table`. Now extracts target table name from requirement doc via regex and writes to state

### Changed

- **Phase 1 SkillContext input standardization**: Changed `SkillContext.input` from raw string to dict, consistent with other phases
- **Phase 4 requirement doc trimming**: Replaced full `requirement_doc` with `requirement_summary` in SQL develop Skill input, saving ~300-2200 chars per run
- **Phase 6 redundant key cleanup**: report node passed 15 keys but Skill only used 7. Removed 8 redundant keys (`requirement_doc`, `ddl_file`, `sql_file`, `review_result`, etc.)

### Removed

- **Dead variable cleanup**: Removed 3 variables never written to state by any node: `coding_style`, `known_tables`, `field_mapping`, along with their template placeholders

### Added

- **Context alignment regression tests**: New `tests/test_context_alignment.py` with 19 tests covering Phase 1-6 node→Skill key passing, ensuring future changes won't break key alignment

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
