"""aqueduct status 平台执行状态节测试。

DP_* 配置存在性 + bdp-cli 登录态新鲜度 + 恢复提示（bdp-cli login + sync），
纯函数无网络探测。红线：cookie 值永不进入输出。

所有用例显式传 env_file 指向不存在的临时路径，避免沾染开发者真实项目 .env。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from src.aqueduct.utils.platform_status import (
    platform_execution_lines as _platform_execution_lines,
)

_TODAY = datetime(2026, 9, 15, 12, 0, 0)


def _env(**overrides: str) -> dict[str, str]:
    env = {
        "DP_BASE_URL": "https://dp.example.com",
        "DP_USER_ID": "01234567",
        "DP_COOKIE": "BDPSESSION=SECRET-VALUE",
    }
    for k, v in overrides.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return env


def _session_file(tmp_path, saved_at: str):
    p = tmp_path / "session.json"
    p.write_text(
        json.dumps({"sessions": {"prod": {"cookie": "c", "savedAt": saved_at}}}),
        encoding="utf-8",
    )
    return p


class TestPlatformExecutionLines:
    def test_all_configured_no_session_shows_sync_hint(self, tmp_path):
        lines = _platform_execution_lines(
            _env(), tmp_path / "none.json", _TODAY, env_file=tmp_path / "no.env"
        )
        text = "\n".join(lines)
        assert "DP_BASE_URL" in text and "DP_COOKIE" in text and "DP_USER_ID" in text
        assert "sync_bdp_session" in text

    def test_fresh_session_shows_saved_date_no_stale_hint(self, tmp_path):
        p = _session_file(tmp_path, (_TODAY - timedelta(days=1)).isoformat())
        text = "\n".join(_platform_execution_lines(_env(), p, _TODAY, env_file=tmp_path / "no.env"))
        assert "2026-09-14" in text
        assert "过期" not in text

    def test_stale_session_shows_relogin_hint(self, tmp_path):
        p = _session_file(tmp_path, (_TODAY - timedelta(days=30)).isoformat())
        text = "\n".join(_platform_execution_lines(_env(), p, _TODAY, env_file=tmp_path / "no.env"))
        assert "bdp-cli login" in text

    def test_missing_cookie_marks_absent(self, tmp_path):
        text = "\n".join(
            _platform_execution_lines(
                _env(DP_COOKIE=None), tmp_path / "n.json", _TODAY, env_file=tmp_path / "no.env"
            )
        )
        assert "✗" in text

    def test_cookie_value_never_in_output(self, tmp_path):
        text = "\n".join(
            _platform_execution_lines(
                _env(), tmp_path / "n.json", _TODAY, env_file=tmp_path / "no.env"
            )
        )
        assert "SECRET-VALUE" not in text

    def test_env_file_fallback_when_os_environ_empty(self, tmp_path):
        """CLI 管道模式 .env 不注入 os.environ——status 须回退读 .env 文件。"""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "DP_BASE_URL=https://dp.example.com\n"
            "DP_COOKIE=BDPSESSION=SECRET-VALUE\n"
            "DP_USER_ID=01234567\n",
            encoding="utf-8",
        )
        text = "\n".join(
            _platform_execution_lines({}, tmp_path / "n.json", _TODAY, env_file=env_file)
        )
        assert text.count("✓") == 3
        assert "SECRET-VALUE" not in text
