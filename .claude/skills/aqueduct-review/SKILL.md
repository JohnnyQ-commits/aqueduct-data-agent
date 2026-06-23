---
name: aqueduct-review
description: >
  Aqueduct SQL 审查模式启动入口。
  DO trigger when 用户要求审查 SQL 变更、对比线上 SQL 和变更版本、
  提到"审查 SQL"、"对比线上版本"、"review SQL"、"aqueduct review"、
  "SQL 变更校验"。
  Do NOT trigger for 从需求文档开发新 SQL（使用 /aqueduct-dev）、
  交付后的需求变更（使用 /change-management）、仅校验 SQL 规范（使用 aqueduct validate）。
allowed-tools:
  - Read
  - Write
  - Edit
  - Grep
  - Glob
  - Bash(python *)
  - Bash(pip *)
  - Bash(aqueduct *)
  - Bash(ls output/*)
  - Bash(dir output/*)
  - mcp__dp-asset-mcp__*
tags:
  - data-engineering
  - code-review
  - sql
  - entry-point
version: "1.0.0"
---

# Aqueduct Review — SQL 审查启动入口

你是 Aqueduct 审查管道的**执行者**。你的职责是：确保环境就绪 → 启动审查管道 → 展示审查结果。

审查模式对比线上版本和变更版本，自动完成差异分析、逻辑校验、DQC 测试、审查报告。

## 执行流程

### Step 1: 环境就绪

验证虚拟环境已激活且 aqueduct 已安装：

```bash
python -c "import aqueduct; print(f'aqueduct {aqueduct.__version__}')"
```

如果失败：
1. 激活虚拟环境：`source .venv/bin/activate`（Windows: `.venv\Scripts\activate`）
2. 安装：`pip install -e ".[dev]"`
3. 再次验证

### Step 2: 确认输入

需要两个 SQL 文件：
- `online.sql` — 线上版本（当前生产环境运行的 SQL）
- `changed.sql` — 变更版本（用户修改后的 SQL）

可选：`-d` 参数描述变更意图（如"新增客户过滤条件"）

### Step 3: 启动审查管道

```bash
aqueduct review {online.sql} {changed.sql} -d "{变更描述}"
```

管道自动执行：需求理解 → 差异比对 → 代码审查 → DQC 测试 → 审查报告

### Step 4: 展示审查结果

管道完成后，读取输出目录的审查报告，向用户展示：

| 文件 | 说明 |
|------|------|
| `Phase5-{name}_审查报告.md` | 代码审查结果（Critical/Warning/INFO） |
| `Phase5-数据质量测试.sql` | 针对变更的 DQC 测试用例 |
| `Phase5-质量仪表盘.md` | 质量覆盖率 |
| `Phase6-交付总报告.md` | 审查总结 |

重点关注审查报告中的 **Critical** 和 **Warning** 级别问题，逐一解读给用户。

## 约束

- **绝对不要**自己读两个 SQL 文件后直接做 diff 分析 — 必须走管道
- **绝对不要**忽略审查报告中的 Critical/Warning
- **绝对不要**在审查发现问题时自行修改 SQL — 让用户决定是否修复
