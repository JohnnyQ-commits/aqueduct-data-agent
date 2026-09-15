"""平台执行状态诊断——纯函数，无网络探测。

供 ``aqueduct status`` 展示 DP_* 配置存在性与 bdp-cli 登录态新鲜度，
失效时给出恢复路径（bdp-cli login + scripts/sync_bdp_session.py）。

凭证卫生红线：cookie 值永不进入输出。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path

# 登录态超过该天数视为陈旧（cookie 实测 1-2 天即可能被顶掉，7 天必然过期）
_STALE_DAYS = 7
_SESSION_PATH = Path.home() / ".bdp" / "session.json"
# 开发检出内默认项目根；安装态下由调用方（_status）显式传 settings.project_root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _saved_at(session_path: Path) -> datetime | None:
    try:
        data = json.loads(session_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = ((data.get("sessions") or {}).get("prod") or {}).get("savedAt", "")
    try:
        saved = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    # 统一转 naive 本地时间，避免与 datetime.now() 相减时 naive/aware 冲突
    return saved.astimezone().replace(tzinfo=None) if saved.tzinfo else saved


def platform_execution_lines(
    env: Mapping[str, str] | None = None,
    session_path: Path | None = None,
    today: datetime | None = None,
    *,
    env_file: Path | None = None,
) -> list[str]:
    """生成平台执行状态行。``env`` 默认取 os.environ（测试注入）。

    CLI 管道模式下 .env 不会注入 os.environ（只有插件模式由 Claude Code
    自动加载），故 ``env_file`` 提供回退：键在两者任一处出现即视为已配置。
    """
    env = os.environ if env is None else env
    session_path = _SESSION_PATH if session_path is None else session_path
    today = today or datetime.now()
    if env_file is None:
        env_file = _PROJECT_ROOT / ".env"
    file_keys = _dotenv_keys(env_file)

    lines: list[str] = []
    for key in ("DP_BASE_URL", "DP_COOKIE", "DP_USER_ID"):
        mark = "✓" if env.get(key) or key in file_keys else "✗"
        lines.append(f"  {key:28s} {mark}")

    saved = _saved_at(session_path)
    if saved is None:
        lines.append("  bdp-cli 登录态:               未找到（建议 bdp-cli login）")
    elif today - saved > timedelta(days=_STALE_DAYS):
        lines.append(f"  bdp-cli 登录态:               {saved:%Y-%m-%d}（已过期）")
        lines.append("  恢复: bdp-cli login + python scripts/sync_bdp_session.py")
    else:
        lines.append(f"  bdp-cli 登录态:               {saved:%Y-%m-%d}")

    if ("DP_COOKIE" in env or "DP_COOKIE" in file_keys) and saved is None:
        lines.append("  提示: cookie 过期时跑 sync_bdp_session 同步，无需开 DevTools")
    return lines


def _dotenv_keys(env_file: Path) -> set[str]:
    """解析 .env 的键名集合（只看存在性，值不落地）。"""
    try:
        return {
            line.split("=", 1)[0].strip()
            for line in env_file.read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.strip().startswith("#")
        }
    except OSError:
        return set()
