# 平台通道适配映射 —— skill 层能力表（默认平台：BDP）

> 本文件是 skill 正文的平台专有内容承载点：skill 正文（SKILL.md / workflow.md /
> verification_checklist.md）保持**通道无关**，执行通道、专有工具、平台坑与本表对接。
> 与代码层 `src/aqueduct/platform/`（PlatformAdapter 六动词协议 + bdp_manifest.json
> capability→transport 清单）同构——**接新平台只改映射文件与 manifest，skill 正文零改动**。

## 一、六动词 ↔ BDP 通道映射

| 动词 | 用途 | BDP 通道 | 说明 |
|------|------|----------|------|
| `table_metadata` | 查表结构/分区/血缘元数据 | 资产 MCP（skill allowed-tools 实例名 `dp-asset-mcp`；代码层 manifest 键名 `bdp-asset-mcp`——同一通道两种配置口径，命名待统一） | 插件模式直接调 MCP 工具，勿写脚本绕路 |
| `sql_execute` | 提交 SQL 并取回结果 | cookie-HTTP 适配器（external-controller；`select 1` 走 health_check，试跑/DQC 实测同通道） | 人工通道 = 平台查询 IDE（单语句限制，见验证坑清单） |
| `dqc_execute` | 执行 DQC 用例 | 同 `sql_execute` | 用例调度接入见第三节 |
| `lineage` | 查血缘 | **未接**（不声明能力） | 管道内血缘由 SQL 解析本地生成 |
| `task_ops` | 任务部署运维 | **未接**（不声明能力） | — |
| `artifact_search` | 检索历史交付物 | **未接**（不声明能力） | — |

能力探测与 L0~L2 降级判据见 data-developer skill《数据验证 Checklist》**第〇节**——先探测通道，再定门禁强度。

## 二、V8 编译验证命令示例（BDP）

宏用字面值替换后，对新增/改动的 SQL 段先编译一遍（只验证编译通过，不要求出结果）：

```bash
bdp-cli ide execute-sql --statement "<SQL，宏已替换为字面值>" --engineName hive --block false
```

其他平台用等价通道（spark-submit 干跑、`EXPLAIN`、CI 语法门禁均可）；L0 无任何通道时降级为静态自检（见 Checklist V8 节）。

**平台坑（BDP 实测沉淀，完整清单见 Checklist 第三节）**：
- bash 双引号传参时 SQL 的 `$[0]` 被算术展开——含 `$` 的 SQL 一律写文件 + `--statement "$(cat 文件)"`
- `bdp-cli` 每次调用有 2-3 分钟启动税，且取结果通道不稳——管道执行走 cookie-HTTP 适配器，bdp-cli 只作登录态获取（`bdp-cli login` → `scripts/sync_bdp_session.py`）与人工抽查

## 三、DQC 用例上线后调度（BDP）

交付时提醒：将 DQC 用例接入**平台数据质量模块的调度**。未接入调度的用例只是文档，不构成持续监控。

## 四、下游链路警告（BDP）

哑替换版临时表数据**不可导入生产下游（如 Doris 正式表）**——哑替换字段为空，会污染线上；仅供结构/新字段验证。
