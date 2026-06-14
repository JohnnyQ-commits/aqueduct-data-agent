# Contributing to Aqueduct

Thank you for your interest in contributing to Aqueduct! This document provides guidelines and information for contributors.

## How to Contribute

### Reporting Bugs

Before creating a bug report, please check existing issues to avoid duplicates. When filing a bug report, include:

- **Clear title** — summarize the issue in one sentence
- **Environment** — Python version, OS, `pip freeze` output
- **Reproduction steps** — minimal steps to trigger the bug
- **Expected vs actual behavior** — what you expected vs what happened
- **Logs** — run with `--verbose` and paste relevant output

Use the [Bug Report template](.github/ISSUE_TEMPLATE/bug_report.yml) for consistency.

### Suggesting Features

Feature requests are welcome. Before submitting:

1. Check existing issues and [Discussions](../../discussions) for similar ideas
2. Clearly describe the **use case** — what problem does this solve?
3. Propose an API or interface if you have one in mind

Use the [Feature Request template](.github/ISSUE_TEMPLATE/feature_request.yml).

### Pull Requests

1. Fork the repository and create your branch from `main`:

   ```bash
   git checkout -b feat/your-feature-name
   # or
   git checkout -b fix/issue-description
   ```

2. Make your changes and add tests:

   ```bash
   # Install dev dependencies
   pip install -e ".[dev]"

   # Run tests
   python -m pytest tests/ -v

   # Run lint
   ruff check src/ tests/
   ruff format --check src/ tests/
   ```

3. Ensure all tests pass and lint is clean before submitting

4. Update documentation if your change affects public APIs or usage

5. Submit the PR and fill in the PR template

#### Commit Message Convention

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add GPT-4 LLM adapter
fix: correct SQL parser handling of nested CTE
docs: update README quick start section
test: add integration tests for memory layer
refactor: extract validation logic into base tool
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`, `style`

### Code Standards

- **Python**: 3.10+, fully type-hinted
- **Formatter**: `ruff` (line-length=100, double quotes, space indent)
- **Lint rules**: E/W/F/I/UP/B/SIM/RUF (see `pyproject.toml` for details)
- **Testing**: all new code must have corresponding tests
- **Docstrings**: Google-style, bilingual (English + Chinese) preferred for public APIs

### Development Workflow

```bash
# 1. Clone your fork
git clone https://github.com/<your-username>/aqueduct.git
cd aqueduct

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
.venv\Scripts\activate     # Windows

# 3. Install with dev dependencies
pip install -e ".[dev]"

# 4. Make changes, test, lint
python -m pytest tests/ -v
ruff check src/ tests/
ruff format src/ tests/

# 5. Push and create PR
git push origin feat/your-feature-name
```

### Architecture Overview

Aqueduct uses a 7-layer architecture. When contributing, understand which layer your change belongs to:

| Layer | Directory | Responsibility |
|-------|-----------|---------------|
| MCP | `src/aqueduct/mcp/` | Standard MCP protocol, platform integration |
| Memory | `src/aqueduct/memory/` | Ontology models, domain knowledge, Top-K recall |
| Agent-DAG | `src/aqueduct/engine/` | StateGraph workflow, node orchestration |
| Skills | `src/aqueduct/skills/` | Business logic, prompt templates (`.tpl.md`) |
| Tools | `src/aqueduct/tools/` | Atomic tools, SQL parsing, template rendering |
| LLM | `src/aqueduct/llm/` | LLM adapters, routing, context management |
| Config | `src/aqueduct/config/` | Settings, environment variables |

Key principle: **nodes do not contain business logic; skills do not contain prompts; prompts live in `.tpl.md` files.**

### Response Commitments

- **First response to Issues**: within 48 hours
- **PR Review**: within 72 hours
- **Security vulnerabilities**: within 24 hours (see [SECURITY.md](SECURITY.md))

Even if we cannot address something immediately, we will acknowledge receipt.

### Getting Help

- [GitHub Discussions](../../discussions) — ask questions, share ideas
- [Existing Issues](../../issues) — search before creating new ones
- [README](../README.md) — project overview and quick start

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
