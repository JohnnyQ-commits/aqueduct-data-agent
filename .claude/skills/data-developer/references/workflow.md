# Aqueduct 工作流详细参考

## 开发模式全流程

### Phase 1: 需求理解
**输入**: 需求文档 (.md)
**处理**:
1. 读取需求文档
2. 查询 MCP 工具获取源表结构
3. 匹配 knowledge/domains/ 业务域
4. **源表验证**：数据探查（唯一性、分区有效性、值域分布、空值比例），结果写入"源表验证"章节
5. 识别歧义点
6. 输出"需求理解摘要 + 问题清单"向用户确认
7. **用户确认后，将回答回写到 Phase1 文档对应问题下方**（引用块 `>` + ✅ 标记），形成闭环
**输出**: `output/{需求名}/Phase1-需求理解摘要.md`（含源表验证 + 问题 + 回答 + 确认结论）

### Phase 2: 设计方案
**输入**: 需求文档 + 业务域上下文
**处理**: 提取取数逻辑、字段映射、依赖关系
**输出**: `output/{需求名}/Phase2-设计方案.md`

### Phase 3: DDL 生成
**输入**: 设计方案
**处理**: 生成 CREATE TABLE 语句
**规范**: 分区字段 `inc_day string`，格式 `YYYYMMDD`，存储 `PARQUET`
**输出**: `output/{需求名}/Phase3-表结构.sql`

### Phase 4: SQL 开发
**输入**: 需求 + DDL + 设计方案 + 业务域上下文
**处理**:
1. 生成 ETL SQL
2. 自动运行 ValidatorTool 校验
3. 自动运行 LineageTool 血缘解析
4. 自动运行 EstimatorTool 成本预估
**输出**:
- `Phase4-{需求名}.sql`
- `Phase4-SQL校验报告.md`
- `Phase4-字段级血缘图.md`
- `Phase4-成本预警.md`

### Phase 4.5: 代码审查
**输入**: SQL + 校验结果 + 业务域上下文
**处理**: 差异比对、需求覆盖度、潜在问题检查
**输出**: `Phase5-{需求名}_审查报告.md`

### Phase 5: DQC 质量测试
**输入**: DDL + SQL + 业务域上下文
**处理**: 生成 5 类测试用例（唯一性/业务反证/一致性/边界/波动）
**输出**:
- `Phase5-数据质量测试.sql`
- `Phase5-质量仪表盘.md`
- `Phase5-DQC执行报告.md`（自动执行时生成）

### Phase 6: 报告交付
**输入**: 所有阶段产出
**处理**:
1. LLM 生成 Design.md
2. 自动生成交付总报告
3. 自动生成知识沉淀文档
4. 运行 ProductivityTool 生成提效看板
**输出**:
- `Phase6-Design.md`
- `Phase6-交付总报告.md`
- `Phase6-知识沉淀.md`
- `Phase6-提效看板.md`

## 交付物清单

| 文件 | 阶段 | 说明 |
|------|------|------|
| Phase1-需求理解摘要.md | 1 | 需求解析 + 歧义点 |
| Phase2-设计方案.md | 2 | 取数逻辑 + 字段映射 |
| Phase3-表结构.sql | 3 | DDL 定义 |
| Phase4-{需求名}.sql | 4 | 核心 ETL 逻辑 |
| Phase4-SQL校验报告.md | 4 | 规范检查结果 |
| Phase4-字段级血缘图.md | 4 | Mermaid 血缘图 |
| Phase4-成本预警.md | 4 | 风险评估 |
| Phase5-{需求名}_审查报告.md | 4.5 | 代码审查报告 |
| Phase5-数据质量测试.sql | 5 | DQC 测试用例 |
| Phase5-质量仪表盘.md | 5 | 测试结果看板 |
| Phase5-DQC执行报告.md | 5 | DQC 自动执行结果（有数据平台时生成） |
| Phase6-Design.md | 6 | 完整设计文档 |
| Phase6-交付总报告.md | 6 | 项目总报告 |
| Phase6-知识沉淀.md | 6 | 经验沉淀 |
| Phase6-提效看板.md | 6 | 效能统计 |

## SQL 编码规范

### 格式要求
- 关键字小写: `select`, `from`, `where`, `group by`, `left join`
- SELECT 字段竖排，4 空格缩进
- WHERE 条件紧凑: ≤3 个条件写一行
- WHERE 多条件换行: `and` 前导 2 空格
- 函数内逗号无空格: `coalesce(a,0)`, `in('a','b')`
- JOIN 和 ON 分行
- 子查询优先: `from (select ... from ...) alias`
- 复杂场景也用派生表：≥4 张源表各自子查询包装后关联；CTE 仅在同一段逻辑被引用 ≥2 次时使用

### 业务规则
- 必须包含分区过滤: `inc_day = '${bizdate}'`
- 可空数值用 `coalesce()` 保护
- 除法用 `nullif(divisor, 0)` 保护
- 禁止 `SELECT *`
- 字符串比较大小写一致
- **同源表多用途合并**: 同一张表因不同用途被读多次时，合并为一个子查询，用 CASE WHEN 条件聚合区分用途（避免重复扫描）

### Smart Fix 自动修复
- `SELECT *` → 展开为具体字段
- 缺少分区过滤 → 自动添加
- 关键字小写 → 统一大写
- 除法未保护 → 添加 NULLIF + NVL
