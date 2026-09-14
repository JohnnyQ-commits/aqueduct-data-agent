# RELEASE.md — 发版 Checklist

> 每次发版（版本号发布 / GitHub Release）按本清单顺序执行。
> 清单是流程约定，不是强制机器——每一项的"为什么"都写在括号里，跳过任何一项都要说明理由并留档。

## 一、代码门禁（提交前已过，发版前复核）

- [ ] **全量测试绿**：`python -m pytest tests/ -q` 记录用例数与结果。
      （发版基线必须可复现；有环境性失败要单独归因留档，不得静默跳过）
- [ ] **lint 干净**：`ruff check src/ tests/` + `ruff format --check src/ tests/`。
      （pre-push 钩子会再拦一次，此处先行自查避免推送期返工）
- [ ] **敏感面人工复核**：`git status` 无 internal/ / output/ / .env / 人员* 等路径；
      发版 diff 范围内抽查无真实表名、工号、内网地址。
      （pre-commit 钩子兜底单次提交，但历史重写成本极高——发版是多提交聚合，值得人工再看一遍；
      敏感字符串完整清单见仓库脱敏批次的登记文档）

## 二、模板变更守门（阶段 2 门禁）

- [ ] **任何 `.tpl.md` diff 必须附 eval 报告**：本版或近期 single-run eval，
      含 prompt 体积、各任务耗时、审查发现数（C/W/Confirm）前后对比；无报告不发布。
      （模板直接改写 LLM 行为契约，历史上 4 次 eval 审查报告因模板/解析不匹配而全盲；
      eval 启动用分离进程：PowerShell `Start-Process ... -WindowStyle Hidden` + 日志重定向，
      会话退出不再带走后台跑）
- [ ] eval 结论有回归时，先修复再发版；网关污染（超时/拒连）导致的失败要标注环境归因，
      与代码回归区分开。

## 三、版本与文档

- [ ] **CHANGELOG**：`[Unreleased]` 条目整理为 `## [x.y.z] - YYYY-MM-DD`；
      新增条目按 Keep a Changelog 分类（Added/Fixed/Changed/...）。
- [ ] **版本号三处同步**：`pyproject.toml` `version`、`src/aqueduct/__init__.py` `__version__`、
      CHANGELOG 标题。（两处代码版本号不一致会让 `pip install -e .` 与运行时版本对不上）
- [ ] 语义化版本判断：破坏性变更（配置键删除/默认值翻转/交付物结构变更）→ 主版本或次版本 +1，
      并在 CHANGELOG 顶部标注 breaking。
- [ ] **Release Notes**：从 CHANGELOG 提取面向使用者的要点（用户视角，不是提交日志罗列），
      中文为主，破坏性变更放最前。

## 四、提交与推送

- [ ] 提交信息一行式，不加 Co-Authored-By（仓库惯例）。
- [ ] **双远端推送**：`git push origin main` + `git push public main`。
      （origin=内部仓库 JohnnyQ-commits/Aqueduct；public=公开仓库 aqueduct-data-agent；
      **绝不推 `data-agent` 远端**——那是旧 Data-agent 仓库，与本项目无关；
      老 `Aqueduct` 库永久私有，不得转公开）
- [ ] **推送后验证远端 SHA**：`git rev-parse origin/main public/main HEAD` 三者一致才算成功。
      （历史教训：`git push ... | tail -1` 管道吃掉退出码，输出 OK 实际失败；
      验证 SHA 是唯一可信判据）
- [ ] 推送网络失败处置：先 curl 探测直连 → 重试 3-5 次 → 不通走 Clash 代理 127.0.0.1:7890。
      （沙箱/代理环境下网络探测有假阴性，以实际 push 结果为准）

## 五、GitHub Release

- [ ] `git tag vx.y.z` + `git push origin vx.y.z`（tag 跟 origin，公开仓库按需同步 tag）。
- [ ] 创建 GitHub Release，正文用 Release Notes（gh release create 或网页）。
- [ ] Release 页面检查渲染：代码块、列表、破坏性变更高亮。

## 六、发版后留档

- [ ] TODO 看板（`临时文件夹/TODO.md` + 质量门禁看板）登记本次发版发现的遗留项。
- [ ] 本次 eval 报告归档于 `evals/runs/<run-id>/`（gitignored，本地留档 + 关键数字抄录 CHANGELOG）。
- [ ] 记忆/知识库同步：跨会话需要记住的决策（不是代码里能查到的）写入对应看板。

---

## 附：历史教训速查（为什么是这几道门）

| 教训 | 事件 | 固化的门 |
|------|------|---------|
| 模板/解析契约漂移 | 审查模板教表格、解析器只认列表行，4 次 eval 审查发现全盲 | 二、模板变更守门 |
| 敏感信息入库 | filter-repo 历史重写（内部文档/真实表名/人员任务域） | 一、敏感面复核 + pre-commit 钩子 |
| 假成功推送 | 管道 tail 吃掉 push 退出码，输出 OK 实际失败 | 四、SHA 三点一致验证 |
| checkpoint 序列化 | _llm_router 不可序列化，断点续跑静默失效 | （代码模式：运行时对象不进 state） |
| 降级假完成 | 降级 Phase 进 checkpoint 前缀，resume 跳过失败 | （P1-3 冻结语义，测试锁定） |
