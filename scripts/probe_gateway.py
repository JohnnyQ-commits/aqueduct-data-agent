"""网关健康探测 — 单次最小 LLM 调用，测延迟与可用性。

用途：稳定性验证（同需求连跑 3 次）前的窗口选择——网关污染窗口
（LLM 超时/重试）里跑出的波动不可归因，必须挑健康窗口。
判读（本机 2026-09-07/08 实测标定）：
    OK   < 180s  健康窗口
    OK   180~400s 慢窗口（可用但三跑耗时长）
    OK   > 400s  拥塞预警
    FAIL / EMPTY 污染窗口

独立脚本，不被管道引用；调用侧建议 `timeout 300 python scripts/probe_gateway.py`。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from aqueduct.llm.base import LLMMessage
from aqueduct.llm.claude import ClaudeLLM


def main() -> int:
    llm = ClaudeLLM()
    t0 = time.time()
    try:
        resp = llm.chat([LLMMessage(role="user", content="回复一个字：好")], max_tokens=64)
        dt = time.time() - t0
        text = (resp.content or "").strip()
        status = "OK" if text else "EMPTY"
        print(f"PROBE {status} {dt:.0f}s model={resp.model} resp={len(text)}chars")
        return 0 if text else 1
    except Exception as e:
        dt = time.time() - t0
        print(f"PROBE FAIL {dt:.0f}s {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
