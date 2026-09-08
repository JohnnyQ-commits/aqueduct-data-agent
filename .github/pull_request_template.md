## Description

<!-- Briefly describe what this PR does and why -->

## Type of Change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to change)
- [ ] Documentation update
- [ ] Refactoring (no functional changes)
- [ ] Test coverage improvement

## Related Issues

<!-- Link related issues: Closes #123, Fixes #456 -->

## Changes Made

<!-- List the specific changes in this PR -->
-
-
-

## Testing

<!-- How was this tested? Include reproduction steps if applicable -->

```bash
# Commands used to test
python -m pytest tests/ -v
ruff check src/ tests/
```

## Prompt/Template Changes (if applicable)

<!-- Required ONLY if this PR touches prompt templates (.tpl.md), skill prompts,
     or the orchestration of LLM calls. Unit tests cannot catch prompt regressions —
     a template can change what the model writes while every test stays green.
     See CONTRIBUTING.md "Prompt/Template Changes" for details. -->

- [ ] This PR **does not** change prompts/templates/LLM orchestration
- [ ] Eval report attached (`python scripts/run_evals.py`, output `evals/runs/report-YYYYMMDD.md`)
- [ ] Scores are not lower than the last baseline (same cases, same scenario)
- [ ] The report's 网关健康 (gateway health) column was checked — a FAIL during gateway anomalies is not a quality regression

## Checklist

- [ ] Code follows the project's style guidelines (`ruff check` passes)
- [ ] Code is formatted (`ruff format` applied)
- [ ] New code has corresponding tests
- [ ] All existing tests pass
- [ ] Documentation has been updated (if applicable)
- [ ] Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
