"""sync_bdp_session 脚本测试——弹窗登录态桥接管道执行凭证。

背景：管道 SQL 执行（试跑门禁/DQC）走 cookie-HTTP 适配器，DP_COOKIE 手工
从浏览器复制、会过期、会被其他登录顶掉。bdp-cli 弹窗登录把登录态存
``~/.bdp/session.json``——本脚本把它同步进 .env，恢复执行能力 = 重新
弹窗登录一次 + 跑本脚本，不用再开 DevTools。

凭证卫生红线：cookie 值永不出现在任何输出里。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "sync_bdp_session.py"
_spec = importlib.util.spec_from_file_location("sync_bdp_session", _SCRIPT)
sync = importlib.util.module_from_spec(_spec)
sys.modules["sync_bdp_session"] = sync
_spec.loader.exec_module(sync)


_FAKE_SESSION = {
    "currentEnv": "prod",
    "sessions": {
        "prod": {
            "cookie": "BDPSESSION=abc123; gray_userId=01234567",
            "baseUrl": "https://data.example.com",
            "savedAt": "2026-09-14T16:00:00",
            "userId": "01234567",
        }
    },
}


class TestExtractSession:
    def test_extracts_cookie_and_user(self):
        info = sync.extract_session(_FAKE_SESSION)
        assert info["cookie"] == "BDPSESSION=abc123; gray_userId=01234567"
        assert info["userId"] == "01234567"
        assert info["savedAt"] == "2026-09-14T16:00:00"

    def test_missing_session_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            sync.load_session(tmp_path / "nope.json")

    def test_missing_env_section(self, tmp_path):
        p = tmp_path / "session.json"
        p.write_text('{"sessions": {"sit": {"cookie": "x"}}}', encoding="utf-8")
        with pytest.raises(KeyError, match="prod"):
            sync.extract_session(sync.load_session(p))

    def test_empty_cookie_rejected(self):
        with pytest.raises(ValueError, match="cookie"):
            sync.extract_session({"sessions": {"prod": {"cookie": ""}}})


class TestSyncEnvFile:
    def test_replaces_existing_dp_lines(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text(
            "DP_BASE_URL=https://data.example.com\n"
            "DP_USER_ID=old\n"
            "DP_COOKIE=stale\n"
            "AQUEDUCT_KNOWLEDGE_DIR=internal/knowledge/domains\n",
            encoding="utf-8",
        )
        changed = sync.sync_env_file(env, cookie="BDPSESSION=new", user_id="01234567")
        assert set(changed) == {"DP_COOKIE", "DP_USER_ID"}
        lines = env.read_text(encoding="utf-8").splitlines()
        assert "DP_COOKIE=BDPSESSION=new" in lines
        assert "DP_USER_ID=01234567" in lines
        assert "AQUEDUCT_KNOWLEDGE_DIR=internal/knowledge/domains" in lines

    def test_appends_when_missing(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("OTHER=1\n", encoding="utf-8")
        sync.sync_env_file(env, cookie="c=1", user_id="u1")
        text = env.read_text(encoding="utf-8")
        assert "DP_COOKIE=c=1" in text
        assert "DP_USER_ID=u1" in text

    def test_dry_run_writes_nothing(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("DP_COOKIE=stale\n", encoding="utf-8")
        before = env.read_text(encoding="utf-8")
        sync.sync_env_file(env, cookie="c=1", user_id="u1", dry_run=True)
        assert env.read_text(encoding="utf-8") == before


class TestCredentialHygiene:
    """红线：cookie 值不得出现在任何 stdout/stderr 输出。"""

    def test_main_output_never_contains_cookie(self, tmp_path, capsys, monkeypatch):
        secret = "BDPSESSION=TOPSECRET-VALUE"
        session_file = tmp_path / "session.json"
        import json

        session_file.write_text(
            json.dumps(
                {
                    "currentEnv": "prod",
                    "sessions": {
                        "prod": {"cookie": secret, "userId": "01234567", "savedAt": "2026-09-14"}
                    },
                }
            ),
            encoding="utf-8",
        )
        env_file = tmp_path / ".env"
        env_file.write_text("DP_COOKIE=old\n", encoding="utf-8")

        rc = sync.main(["--session-path", str(session_file), "--env-path", str(env_file)])

        assert rc == 0
        captured = capsys.readouterr()
        assert "TOPSECRET-VALUE" not in captured.out
        assert "TOPSECRET-VALUE" not in captured.err
        assert "DP_COOKIE=BDPSESSION=TOPSECRET" not in captured.out

    def test_main_missing_session_exit_1(self, tmp_path, capsys):
        rc = sync.main(
            [
                "--session-path",
                str(tmp_path / "nope.json"),
                "--env-path",
                str(tmp_path / ".env"),
            ]
        )
        assert rc == 1
        assert "未找到" in capsys.readouterr().out
