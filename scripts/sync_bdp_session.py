"""bdp-cli 弹窗登录态 → .env DP_COOKIE 同步脚本。

管道 SQL 执行（试跑门禁 / DQC 真跑）走 cookie-HTTP 适配器（dp_client.py），
DP_COOKIE 手工从浏览器 DevTools 复制、会过期、会被其他端登录顶掉——这是
管道平台链路唯一的纯手工环节。bdp-cli 弹窗登录（bdp-cli login）把同一份
登录态存 ~/.bdp/session.json：本脚本把它同步进 .env，恢复执行能力 =
重新弹窗登录一次 + 跑本脚本。

用法：
    python scripts/sync_bdp_session.py            # 同步 prod 会话
    python scripts/sync_bdp_session.py --dry-run  # 只预览不写

凭证卫生：cookie 值只进 .env，任何输出只打印掩码摘要。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

DEFAULT_SESSION_PATH = Path.home() / ".bdp" / "session.json"
DEFAULT_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_session(session_path: Path) -> dict:
    """读取并解析 session.json。"""
    if not session_path.is_file():
        raise FileNotFoundError(
            f"未找到 bdp-cli 登录态文件: {session_path}——"
            f"请先执行 bdp-cli login --env prod 完成弹窗登录"
        )
    return json.loads(session_path.read_text(encoding="utf-8"))


def extract_session(data: dict, env: str = "prod") -> dict:
    """抽取目标环境的会话信息（cookie/userId/savedAt）。"""
    sessions = data.get("sessions") or {}
    if env not in sessions:
        raise KeyError(f"session.json 中没有 {env!r} 环境的会话，现有: {sorted(sessions)}")
    section = sessions[env] or {}
    cookie = (section.get("cookie") or "").strip()
    if not cookie:
        raise ValueError(f"{env} 环境的会话没有 cookie——请重新 bdp-cli login")
    return {
        "cookie": cookie,
        "userId": section.get("userId", ""),
        "savedAt": section.get("savedAt", "未知时间"),
    }


def sync_env_file(env_path: Path, cookie: str, user_id: str, *, dry_run: bool = False) -> list[str]:
    """把 cookie/userId 写进 .env 的 DP_COOKIE/DP_USER_ID 行，返回变更键列表。"""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
    changed: list[str] = []
    updates = {"DP_COOKIE": f"DP_COOKIE={cookie}", "DP_USER_ID": f"DP_USER_ID={user_id}"}
    seen: set[str] = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0]
        if key in updates:
            if line != updates[key]:
                lines[i] = updates[key]
                changed.append(key)
            seen.add(key)
    for key, val in updates.items():
        if key not in seen:
            lines.append(val)
            changed.append(key)
    if changed and not dry_run:
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed


def _mask(value: str, keep: int = 12) -> str:
    return value[:keep] + f"...（共 {len(value)} 字符）"


def main(argv: list[str] | None = None) -> int:
    # Windows GBK 控制台兼容：强制 UTF-8 输出（pytest/capsys 环境下跳过）
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(OSError, ValueError):
                stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="bdp-cli 登录态同步到 .env DP_COOKIE")
    parser.add_argument("--session-path", type=Path, default=DEFAULT_SESSION_PATH)
    parser.add_argument("--env-path", type=Path, default=DEFAULT_ENV_PATH)
    parser.add_argument("--dry-run", action="store_true", help="只预览变更，不写文件")
    args = parser.parse_args(argv)

    try:
        info = extract_session(load_session(args.session_path))
        changed = sync_env_file(args.env_path, info["cookie"], info["userId"], dry_run=args.dry_run)
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError) as e:
        print(f"[FAIL] {e}")
        return 1

    tag = "（dry-run 预览，未写入）" if args.dry_run else ""
    if changed:
        print(f"[OK] 已更新 {', '.join(changed)} {tag}")
    else:
        print(f"[OK] .env 已是最新，无需变更 {tag}")
    print(f"     登录态时间: {info['savedAt']}；cookie 摘要: {_mask(info['cookie'])}")
    print("     管道执行能力已就绪——可跑 scripts/smoke_trial_gate.py 验证")
    return 0


if __name__ == "__main__":
    sys.exit(main())
