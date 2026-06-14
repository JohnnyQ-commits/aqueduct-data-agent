# Aqueduct Architecture

> **7-Layer Architecture for Data Warehouse Automation**

Aqueduct is built on a strict 7-layer architecture with clear separation of concerns. Each layer has a single responsibility and communicates only through well-defined interfaces.

---

## Architecture Overview

```
┌────────────────────────────────────────────────────────────────────┐
│  Layer 7  MCP          Standard protocol / .mcp.json / Any platform │
├────────────────────────────────────────────────────────────────────┤
│  Layer 6  Memory       Ontology models / Domain knowledge / Top-K   │
├────────────────────────────────────────────────────────────────────┤
│  Layer 5  Agent-DAG    StateGraph / 8-node DAG / Dual-mode workflow │
├────────────────────────────────────────────────────────────────────┤
│  Layer 4  Skills       7 core skills / .tpl.md prompts / Extensible │
├────────────────────────────────────────────────────────────────────┤
│  Layer 3  Tools        9 atomic tools / SQL parser / Registry       │
├────────────────────────────────────────────────────────────────────┤
│  Layer 2  LLM          BaseLLM ABC / 3-tier router / Context mgmt   │
├────────────────────────────────────────────────────────────────────┤
│  Layer 1  Config       Pydantic-settings / .env / Type-safe         │
└────────────────────────────────────────────────────────────────────┘
```

---

## Layer 1: Configuration

**Directory**: `src/aqueduct/config/`

**Responsibility**: Centralized, type-safe configuration management.

**Key Components**:

- `settings.py` — Pydantic-settings based configuration
- `.env` — Environment variables (DP credentials, API tokens)
- `pyproject.toml` — Project metadata and dependencies

**Design Principles**:

- All configuration is validated at startup
- Sensitive data (cookies, tokens) loaded from environment
- Type hints provide IDE autocomplete and catch errors early

**Example**:

```python
from aqueduct.config import Settings

settings = Settings()  # Loads from .env automatically
print(settings.dp_base_url)  # Type-safe access
```

---

## Layer 2: LLM Abstraction

**Directory**: `src/aqueduct/llm/`

**Responsibility**: Abstract LLM provider details and manage model routing.

**Key Components**:

- `base.py` — `BaseLLM` abstract base class
- `claude.py` — Claude adapter (SDK + CLI backends)
- `router.py` — 3-tier model router (Haiku/Sonnet/Opus)
- `context.py` — Token budget management and prompt truncation

**Design Principles**:

- Provider-agnostic interface — swap LLMs without changing business logic
- Cost optimization: route simple tasks to cheaper models
- Context window management prevents token overflow

**Model Routing Strategy**:
| Tier | Model | Use Case |
|------|-------|----------|
| LIGHT | Haiku | Simple parsing, formatting |
| MEDIUM | Sonnet | SQL generation, code review |
| HEAVY | Opus | Complex reasoning, architecture design |

**Extending**:

```python
from aqueduct.llm.base import BaseLLM

class GPT4LLM(BaseLLM):
    async def generate(self, prompt: str, **kwargs) -> str:
        # Your implementation
        pass
```

---

## Layer 3: Tools

**Directory**: `src/aqueduct/tools/`

**Responsibility**: Atomic, reusable utilities for SQL processing and validation.

**Key Components**:
| Tool | Function |
|------|----------|
| `validator.py` | SQL syntax and rule validation (7 checks) |
| `lineage.py` | Field-level lineage extraction + Mermaid diagram |
| `estimator.py` | Cost estimation and risk assessment |
| `dqc.py` | DQC test parsing and quality dashboard |
| `semantic.py` | Semantic model documentation generator |
| `design.py` | Design document renderer |
| `sync.py` | DDL synchronization utility |
| `batch_query.py` | Batch metadata queries |
| `productivity.py` | Productivity metrics calculation |

**Design Principles**:

- Each tool is a pure function — no side effects
- Tools are stateless and composable
- All tools inherit from `BaseTool` ABC

**Example**:

```python
from aqueduct.tools.validator import validate_sql

errors = validate_sql(sql, strict=True)
if errors:
    print(f"Found {len(errors)} violations")
```

---

## Layer 4: Skills

**Directory**: `src/aqueduct/skills/`

**Responsibility**: Business logic for each workflow phase, with prompts decoupled from code.

**Key Components**:
| Skill | Phase | Responsibility |
|-------|-------|----------------|
| `requirement_clarify.py` | 1 | Parse requirements, identify ambiguities |
| `design_scheme.py` | 2 | Generate table design |
| `ddl_generate.py` | 3 | Produce CREATE TABLE statements |
| `sql_develop.py` | 4 | Write ETL SQL |
| `code_review.py` | 4.5 | Validate against 7 rules |
| `dqc_quality.py` | 5 | Generate DQC test cases |
| `report_delivery.py` | 6 | Compile delivery report |

**Prompt Templates**: `src/aqueduct/skills/prompt/`

- Each skill has a corresponding `.tpl.md` file
- Prompts are Markdown with Jinja2 placeholders
- Decoupled from Python code for easy iteration

**Design Principles**:

- Skills contain business logic, not prompts
- Prompts live in `.tpl.md` files
- Skills call Tools for atomic operations
- Skills are orchestrated by Engine nodes

---

## Layer 5: Agent-DAG Engine

**Directory**: `src/aqueduct/engine/`

**Responsibility**: Workflow orchestration using LangGraph-compatible StateGraph.

**Key Components**:

- `state.py` — `WorkflowState` TypedDict (workflow context)
- `workflow.py` — StateGraph definition and compilation
- `nodes/` — 8 node modules (state assembly only)
- `recovery.py` — Error recovery with exponential backoff

**Workflow Modes**:

**Dev Mode** (full pipeline):

```
requirement → design → ddl → sql → review → dqc → report
   (Phase 1)   (2)    (3)   (4)    (4.5)   (5)    (6)
```

**Review Mode** (change validation):

```
requirement → review → dqc → report
   (Phase 1)   (4.5)   (5)    (6)
```

**Design Principles**:

- Nodes only assemble state and call Skills
- No business logic in nodes
- StateGraph API compatible with LangGraph
- Dual-mode workflow (dev/review) shares same graph structure

**State Flow**:

```python
class WorkflowState(TypedDict):
    requirement: str
    design: dict
    ddl: str
    sql: str
    review_result: dict
    dqc_tests: str
    report: dict
    # ... metadata
```

---

## Layer 6: Memory

**Directory**: `src/aqueduct/memory/`

**Responsibility**: Ontology-driven knowledge base with semantic recall.

**Key Components**:

- `domain.py` — `DomainModel` Pydantic model (entities, relationships, metrics)
- `store.py` — `MemoryStore` with LRU caching
- `recall.py` — `KnowledgeRecall` with Top-K semantic search

**Knowledge Structure**:

```json
{
  "domain_id": "ecommerce_order",
  "entities": [
    {
      "name": "Order",
      "primary_key": "order_id",
      "source_table": "dw.dwd_order_info_di",
      "attributes": [...]
    }
  ],
  "relationships": [...],
  "metrics": [...],
  "axioms": [...]
}
```

**Design Principles**:

- Domain knowledge stored as typed JSON models
- Markdown (`semantic-model.md`) generated for human audit
- Top-K recall retrieves relevant knowledge based on requirement
- LRU cache prevents redundant file reads

---

## Layer 7: MCP Integration

**Directory**: `src/aqueduct/mcp/`

**Responsibility**: Platform-agnostic data platform integration via Model Context Protocol.

**Key Components**:

- `client.py` — MCP client implementation
- `tools.py` — MCP tool registry
- `.mcp.json` — Server configuration

**Design Principles**:

- Standard MCP protocol — works with any MCP server
- Configuration-driven — no code changes to add new platforms
- Users configure their data platform in `.mcp.json`

**Example Configuration**:

```json
{
  "mcpServers": {
    "my-data-platform": {
      "command": "npx",
      "args": ["-y", "@my-org/mcp-server"],
      "env": {
        "PLATFORM_URL": "https://platform.example.com",
        "API_TOKEN": "your-token"
      }
    }
  }
}
```

---

## Data Flow

### Dev Mode Pipeline

```
1. User provides requirement.md
   ↓
2. Engine loads WorkflowState
   ↓
3. Phase 1: Requirement Clarify
   - Skill calls LLM to parse requirement
   - Memory recalls relevant domain knowledge
   - State updated with structured requirement
   ↓
4. Phase 2: Design Scheme
   - Skill generates table design
   - Tools validate naming conventions
   ↓
5. Phase 3: DDL Generate
   - Skill produces CREATE TABLE statements
   - Lineage tool generates Mermaid diagram
   ↓
6. Phase 4: SQL Develop
   - Skill writes ETL SQL
   - Validator tool checks syntax
   - Estimator tool assesses cost
   ↓
7. Phase 4.5: Code Review
   - Review skill validates 7 rules
   - Iterates with SQL skill if issues found
   ↓
8. Phase 5: DQC Quality
   - DQC skill generates test cases
   - DQC tool parses and validates tests
   ↓
9. Phase 6: Report Delivery
   - Report skill compiles delivery package
   - Productivity tool calculates metrics
   ↓
10. Output: 11 deliverables in output/
```

---

## Extension Points

### Adding a New Skill

1. Create skill class in `src/aqueduct/skills/`
2. Create prompt template in `src/aqueduct/skills/prompt/`
3. Register skill in `src/aqueduct/skills/__init__.py`
4. Add node in `src/aqueduct/engine/nodes/`
5. Update workflow graph in `src/aqueduct/engine/workflow.py`

### Adding a New Tool

1. Create tool class in `src/aqueduct/tools/`
2. Inherit from `BaseTool`
3. Implement `execute()` method
4. Register in `src/aqueduct/tools/__init__.py`

### Adding a New LLM Adapter

1. Create adapter in `src/aqueduct/llm/`
2. Inherit from `BaseLLM`
3. Implement `generate()` method
4. Add routing logic in `router.py`

---

## Design Decisions

### Why 7 Layers?

Each layer solves a distinct problem:

- **Config**: Type-safe configuration
- **LLM**: Provider abstraction
- **Tools**: Reusable utilities
- **Skills**: Business logic
- **Engine**: Orchestration
- **Memory**: Knowledge management
- **MCP**: Platform integration

### Why Decouple Prompts?

Prompts change frequently. By storing them in `.tpl.md` files:

- Non-developers can edit prompts
- Prompts are version-controlled separately
- Easy A/B testing of prompt variants

### Why StateGraph?

LangGraph's StateGraph provides:

- Declarative workflow definition
- Built-in state management
- Conditional branching and loops
- Compatibility with LangGraph ecosystem

---

## References

- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
