"""UI 会话状态与线程握手 — 纯标准库，不依赖 gradio。

线程模型（单会话演示）::

    UI 线程 (Gradio)                     worker 线程 (daemon)
    ────────────────                     ───────────────────
    生成器循环:                           Aqueduct().dev(...)  [interactive=True]
      events.get(timeout=0.5)  <──────    on_progress → events.put(("progress", …))
      yield 组件更新                       on_confirm → events.put(("confirm", …))
                                           answers.get()   ← 阻塞管道线程等 UI 作答
    确认按钮 → answers.put({...})  ──────→
                                           格式化 QA + 追加文件 + 更新 state
                                           events.put(("done", result))

事件契约（events 队列）：
  ("progress", idx, total) — 阶段开始（idx 1-based；auto-resume 重跑 idx==1 时进度归零）
  ("confirm", ConfirmRequest) — Phase 1 完成，等待作答
  ("confirm_resolved", None) — 作答已受理，面板可关闭
  ("done", AqueductResult) — 终态（正常完成 / halt / 异常合成），生成器收到即 return

回答契约（answers 队列）：{"action": "continue"|"stop", "answers": [str]}
"""

from __future__ import annotations

import queue
from dataclasses import dataclass, field

from ..core import Aqueduct, AqueductResult
from ..engine.state import WorkflowState
from ..utils.qa import append_qa_to_file, format_qa_section, parse_questions

# dev 管道 7 阶段（与 core.py _DEV_PHASES 对应）及其中文显示名
DEV_PHASE_LABELS: list[tuple[str, str]] = [
    ("requirement", "需求理解"),
    ("design", "设计方案"),
    ("ddl", "DDL 生成"),
    ("sql", "SQL 开发"),
    ("review", "代码审查"),
    ("dqc", "DQC 质检"),
    ("report", "报告交付"),
]


@dataclass
class ConfirmRequest:
    """Phase 1 确认请求：需求摘要 + 待确认问题（供 UI 渲染问答面板）。"""

    summary: str
    questions: list[str] = field(default_factory=list)


def pair_answers(questions: list[str], answer_lines: list[str]) -> list[dict]:
    """按行对齐回答；缺行补「(未回答)」，多余行忽略。"""
    pairs = []
    for i, q in enumerate(questions):
        answer = answer_lines[i].strip() if i < len(answer_lines) else ""
        pairs.append({"question": q, "answer": answer or "(未回答)"})
    return pairs


class UiSession:
    """单次运行会话：持有双向队列与进度游标，worker 与 UI 各持引用。

    进度渲染依据 current_idx 单值推进——phase N+1 开始即视为 N 完成
    （on_phase_complete 不经 dev() 暴露，不依赖它）。
    """

    def __init__(self) -> None:
        self.events: queue.Queue = queue.Queue()
        self.answers: queue.Queue = queue.Queue()
        self.current_idx: int = 0

    # ---- worker 侧回调（传给 Aqueduct.dev）----

    def on_progress(self, phase_name: str, idx: int, total: int, state: WorkflowState) -> None:
        if idx == 1:
            # 自动续跑（auto-resume）重跑时进度归零重推
            self.current_idx = 0
        self.current_idx = idx
        self.events.put(("progress", idx, total))

    def on_confirm(self, state: WorkflowState) -> bool:
        """Phase 1 交互确认：推送确认请求后阻塞等待 UI 作答。

        返回 True 续跑 / False 停止（halt）。
        """
        summary = state.get("requirement_summary", "")
        if not summary:
            return True

        questions = parse_questions(summary)
        self.events.put(("confirm", ConfirmRequest(summary=summary, questions=questions)))
        resp = self.answers.get()  # 阻塞管道线程直至 UI 作答
        if resp.get("action") != "continue":
            return False

        qa_pairs = pair_answers(questions, resp.get("answers") or [])
        append_qa_to_file(state, qa_pairs)
        state["requirement_summary"] = summary.rstrip() + format_qa_section(qa_pairs)
        self.events.put(("confirm_resolved", None))
        return True

    # ---- UI 侧调用 ----

    def submit(self, action: str, answers: list[str]) -> None:
        """UI 作答入口：确认继续（带逐行回答）或停止。"""
        self.answers.put({"action": action, "answers": answers})

    # ---- worker 线程入口 ----

    def run(
        self,
        requirement_text: str,
        output_dir: str | None = None,
        resume: bool = False,
    ) -> None:
        """daemon 线程入口：跑完整 dev 管道，终态（或异常合成结果）必落 done 事件。"""
        try:
            result = Aqueduct().dev(
                requirement_text,
                output_dir=output_dir,
                interactive=True,
                on_confirm=self.on_confirm,
                on_progress=self.on_progress,
                resume=resume,
            )
        except Exception as e:  # 生成器必须收到终态才能退出，任何异常都合成 done 结果
            result = AqueductResult(
                {"errors": [f"dev() 异常: {e!s}"], "artifacts": []}, halted=True
            )
        self.events.put(("done", result))

    # ---- 进度渲染（纯函数，供 app.py 调用）----

    def render_progress(self) -> str:
        """渲染 7 阶段进度 Markdown：之前 ✅、当前 ▶️、之后 ⬜。"""
        lines = ["## 管道进度", ""]
        for i, (_name, label) in enumerate(DEV_PHASE_LABELS, 1):
            if i < self.current_idx:
                mark = "✅"
            elif i == self.current_idx:
                mark = "▶️"
            else:
                mark = "⬜"
            lines.append(f"- {mark} {i}/7 {label}")
        return "\n".join(lines)
