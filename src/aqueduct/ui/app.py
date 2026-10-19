"""最小 Gradio Web UI — Aqueduct 数据开发助手。

线程模型：worker 线程跑 Aqueduct().dev()（阻塞），经 UiSession 的
events/answers 两个 queue 与 UI 握手（见 session.py 模块文档）。
本模块是包内唯一 import gradio 的地方。

已知限制（单会话演示定位）：
- 关闭浏览器后 worker 线程（daemon）仍会跑完并落盘，但 UI 状态不保留；
- 无用户系统、无历史任务、无并发运行（运行中开始按钮禁用）。

用法::

    from aqueduct.ui.app import launch
    launch(requirement_text="...", output_dir="output/my_req")

或 CLI：aqueduct dev --ui
"""

from __future__ import annotations

import queue

import gradio as gr

from .session import ConfirmRequest, UiSession


def _format_confirm_md(req: ConfirmRequest) -> str:
    """确认面板 Markdown：摘要 + 编号问题（回答框每行对应一题）。"""
    lines = ["### Phase 1 需求理解摘要", "", req.summary.strip(), ""]
    if req.questions:
        lines.append(f"### 待确认问题（{len(req.questions)} 条，回答框内**每行对应一题**）")
        lines.extend(f"{i}. {q}" for i, q in enumerate(req.questions, 1))
    else:
        lines.append("无待确认问题，直接点击「确认继续」。")
    return "\n".join(lines)


def create_app(
    requirement_text: str = "",
    output_dir: str | None = None,
    resume: bool = False,
    requirement_name: str | None = None,
) -> gr.Blocks:
    """构建 Gradio Blocks 应用（不启动服务；服务由 launch() 负责）。"""
    with gr.Blocks(title="Aqueduct 数据开发助手") as demo:
        gr.Markdown(
            "# Aqueduct 数据开发助手\n\n粘贴需求文档（或上传 .md），一键生成 15 个标准化交付物。"
        )
        session_state = gr.State(value=None)
        # 应用级单例槽（非页面级）：worker 与 UI 生命周期解耦——
        # 2026-10-16 实录：QA 等待中刷新页面 → SSE 断开、gr.State 丢失，
        # worker 不知情继续跑/永久阻塞在 answers.get()；再次点开始 →
        # 双 worker 并发写同一输出目录 + 任务日志双写。
        # holder["session"] = 运行中会话；holder["last_result"] = 终态快照
        holder: dict = {"session": None, "last_result": None}
        demo._aq_session_holder = holder  # 测试与诊断入口

        with gr.Row():
            with gr.Column():
                req_text = gr.Textbox(
                    label="需求文本",
                    lines=12,
                    value=requirement_text,
                    placeholder="在此粘贴需求文档内容…",
                )
                req_file = gr.File(label="或上传需求文档 (.md)", file_types=[".md", ".txt"])
                btn_start = gr.Button("开始运行", variant="primary")

            with gr.Column():
                progress_md = gr.Markdown("点击「开始运行」后此处显示 7 阶段进度。")
                status_md = gr.Markdown("")

        with gr.Group(visible=False) as qa_group:
            qa_md = gr.Markdown("")
            answers_tb = gr.Textbox(label="回答（每行对应一个问题，按顺序作答）", lines=4)
            with gr.Row():
                btn_confirm = gr.Button("确认继续", variant="primary")
                btn_stop = gr.Button("停止")

        deliverables = gr.File(label="交付物下载", interactive=False, file_count="multiple")

        def _final_update(session: UiSession, result) -> dict:
            """done 事件的终态组件更新：状态块 + 交付物 + 恢复开始按钮。"""
            if result.success:
                banner = f"✅ 管道完成，共 {len(result.artifacts)} 个交付物。"
            elif result.halted:
                banner = "⏹ 已停止（可在上方修改需求后重新开始）。"
            else:
                banner = "❌ 管道结束，存在错误。"
            lines = [banner, ""]
            # 降级可见性：success=True 也可能带着未验证降级（试跑/血缘/DQC
            # 环境性失败被静默吞掉的实录教训），必须显式提示。惰性导入避免
            # UI 模块加载即拉起引擎依赖链
            from ..engine.nodes.report import format_degradation_banner

            degradation_banner = format_degradation_banner(result.degradations)
            if degradation_banner:
                lines.extend(degradation_banner.split("\n"))
                lines.append("")
            if result.errors:
                lines.append("**错误记录**")
                lines.extend(f"{i}. {e}" for i, e in enumerate(result.errors, 1))
                lines.append("")
            return {
                status_md: gr.update(value="\n".join(lines)),
                progress_md: gr.update(value=session.render_progress()),
                deliverables: gr.update(value=list(result.artifacts or [])),
                btn_start: gr.update(interactive=True),
                session_state: None,
            }

        def _read_upload(file_obj):
            """上传 .md → 预填需求文本框。"""
            if file_obj is None:
                return gr.update()
            return gr.update(value=file_obj.read_text(encoding="utf-8"))

        def _start(text: str, _state_session):
            """开始运行：起 worker 线程，生成器轮询事件流驱动组件更新。

            防重复开始：应用级单例仍存活时拒绝新起 worker（双 worker
            并发写同一输出目录实录教训），并把存活 session 重新挂回
            gr.State 恢复作答通道。
            """
            existing = holder["session"]
            if existing is not None and existing.is_alive():
                yield {
                    progress_md: gr.update(value=existing.render_progress()),
                    status_md: gr.update(
                        value="⚠️ 已有任务在运行中，请等待完成或在问答面板点「停止」。"
                        "刷新页面可重新接上运行中的任务。"
                    ),
                    btn_start: gr.update(interactive=False),
                    session_state: existing,
                }
                return

            session = UiSession()
            holder["session"] = session
            holder["last_result"] = None
            session.start_worker(text, output_dir, resume, requirement_name)

            running = {
                progress_md: gr.update(value=session.render_progress()),
                status_md: gr.update(value=""),
                btn_start: gr.update(interactive=False),
                qa_group: gr.update(visible=False),
                deliverables: gr.update(value=None),
                session_state: session,
            }
            yield from _consume(session, running)

        def _restore():
            """页面加载恢复：接回运行中的单例 session（修复刷新丢进度/QA 面板）。

            恢复 = 重挂 session 到 gr.State + 继续消费事件流（生成器形态），
            不是静态快照——done 后照常渲染终态。无运行中任务时若存在上一轮
            终态快照则渲染之，否则不更新（首屏默认）。
            """
            session = holder["session"]
            if session is not None and session.is_alive():
                first = {
                    progress_md: gr.update(value=session.render_progress()),
                    status_md: gr.update(value="（已重新接上运行中的任务）"),
                    btn_start: gr.update(interactive=False),
                    session_state: session,
                }
                if session.pending_confirm is not None:
                    first[qa_group] = gr.update(visible=True)
                    first[qa_md] = gr.update(value=_format_confirm_md(session.pending_confirm))
                    first[answers_tb] = gr.update(value="")
                yield from _consume(session, first)
                return
            last = holder.get("last_result")
            if last is not None and last[0] is session:
                yield _final_update(session, last[1])

        def _consume(session: UiSession, first_yield: dict):
            """事件消费循环：_start 与 _restore 共用（恢复后事件不丢）。"""
            yield first_yield

            while True:
                try:
                    ev, *payload = session.events.get(timeout=0.5)
                except queue.Empty:
                    continue
                if ev == "progress":
                    yield {progress_md: gr.update(value=session.render_progress())}
                elif ev == "confirm":
                    yield {
                        qa_group: gr.update(visible=True),
                        qa_md: gr.update(value=_format_confirm_md(payload[0])),
                        answers_tb: gr.update(value=""),
                    }
                elif ev == "confirm_resolved":
                    yield {qa_group: gr.update(visible=False)}
                elif ev == "done":
                    holder["last_result"] = (session, payload[0])
                    yield _final_update(session, payload[0])
                    return

        def _confirm(text: str, session: UiSession | None):
            if session is not None:
                session.submit("continue", [line for line in text.splitlines() if line.strip()])
            return gr.update()

        def _stop(session: UiSession | None):
            if session is not None:
                session.submit("stop", [])
            return gr.update()

        req_file.upload(_read_upload, inputs=req_file, outputs=req_text)
        btn_start.click(
            _start,
            inputs=[req_text, session_state],
            outputs=[
                progress_md,
                status_md,
                btn_start,
                qa_group,
                deliverables,
                session_state,
                qa_md,
                answers_tb,
            ],
            concurrency_limit=1,
        )
        btn_confirm.click(_confirm, inputs=[answers_tb, session_state], outputs=[])
        btn_stop.click(_stop, inputs=[session_state], outputs=[])
        demo.load(
            _restore,
            inputs=None,
            outputs=[progress_md, status_md, btn_start, qa_group, qa_md, answers_tb, session_state],
        )

    return demo


def launch(
    requirement_text: str = "",
    output_dir: str | None = None,
    resume: bool = False,
    requirement_name: str | None = None,
) -> None:
    """CLI --ui 入口：构建应用并启动本地服务（自动开浏览器）。"""
    create_app(requirement_text, output_dir, resume, requirement_name).launch(inbrowser=True)
