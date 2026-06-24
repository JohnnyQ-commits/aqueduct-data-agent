# Security Policy

## Supported Versions

As Aqueduct is in active development (pre-1.0), we only support the latest release.

| Version | Supported |
|---------|-----------|
| 0.4.x   | ✅        |
| < 0.4   | ❌        |

## Reporting a Vulnerability

If you discover a security vulnerability in Aqueduct, please report it responsibly.

**Do NOT create a public GitHub issue for security vulnerabilities.**

### How to Report

Send an email to: **<security@aqueduct.dev>**

Please include:

- Description of the vulnerability
- Steps to reproduce
- Potential impact assessment
- Suggested fix (if any)

### What to Expect

- **Acknowledgment**: We will confirm receipt within 24 hours
- **Assessment**: We will evaluate the severity and scope within 72 hours
- **Fix timeline**: We will provide an estimated fix timeline based on severity
- **Disclosure**: We will coordinate public disclosure with you after a fix is released

### Scope

The following are in scope:

- Core framework code (`src/aqueduct/`)
- CLI tool (`aqueduct` command)
- MCP integration layer
- LLM adapter layer
- Configuration handling (`.env`, `pyproject.toml`)

The following are out of scope:

- Vulnerabilities in third-party dependencies (report them upstream)
- Social engineering attacks
- Denial of service attacks
- Physical security

## Security Best Practices for Users

1. **Never commit `.env` files** — use `.env.example` as a template
2. **Rotate credentials regularly** — DP cookies, API tokens, etc.
3. **Use virtual environments** — isolate dependencies per project
4. **Keep dependencies updated** — run `pip audit` periodically
5. **Review MCP Server configurations** — `.mcp.json` may contain sensitive URLs/tokens

## Security Audit

Aqueduct has not yet undergone a formal security audit. If you use this project in production, we recommend:

- Running `pip-audit` on your dependencies
- Reviewing all `.env` variables before deployment
- Restricting MCP Server access to trusted networks
