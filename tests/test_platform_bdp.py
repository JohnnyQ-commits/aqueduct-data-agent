"""bdp 专用 adapter 测试 — 声明式能力映射 + 传输探针 + DP 凭证 .env 回退。

设计来源：知识库《平台适配器架构-开源通用化设计》"capability→transport
映射"批（原计划 platform.yaml + pyyaml——pyyaml 非依赖，改 JSON 清单零依赖
等价）。

与 auto 的分工：
- auto = 探测既有隐式谓词（MCP 配置 + execution_enabled），零行为变化
- bdp = 清单声明 capability→transport，再探 transport 是否就绪——
  sql_execute 的探针是「凭证可解析」而非 settings 布尔，比 auto 诚实：
  凭证缺失时不再声明能力（门禁直接跳过而非执行时报错）

附带修复：CLI 管道模式 .env 不注入 os.environ（2026-09-15 status 批实证），
DataPlatformAdapter 只读 os.environ → 裸终端 aqueduct dev 门禁放行但执行
时凭证缺失。load_dp_env() 统一解析口径（os.environ 优先，.env 回退，
不污染 os.environ）。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.aqueduct.config.settings import get_settings
from src.aqueduct.platform import ALL_CAPABILITIES, Capability
from src.aqueduct.platform.bdp import BDPAdapter, _load_manifest

_MANIFEST_PATH = (
    Path(__file__).resolve().parent.parent / "src" / "aqueduct" / "platform" / "bdp_manifest.json"
)


@pytest.fixture(autouse=True)
def _reset_adapter_singleton():
    from src.aqueduct.platform import reset_platform_adapter

    reset_platform_adapter()
    yield
    reset_platform_adapter()


@pytest.fixture
def isolated_project_root(tmp_path, monkeypatch):
    """把 settings.project_root 钉到 tmp_path（.env 隔离）。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "project_root", tmp_path)
    return tmp_path


# ============ 清单文件 ============


class TestBdpManifest:
    def test_manifest_exists_and_valid_json(self):
        manifest = _load_manifest()
        assert manifest["platform"] == "bdp"

    def test_declared_capabilities_are_known_verbs(self):
        caps = set(_load_manifest()["capabilities"])
        assert caps <= ALL_CAPABILITIES
        assert "table_metadata" in caps
        assert "sql_execute" in caps
        assert "dqc_execute" in caps

    def test_unimplemented_verbs_never_declared(self):
        """lineage/task_ops/artifact_search 无实现，清单不得声明（不谎报）。"""
        caps = set(_load_manifest()["capabilities"])
        assert not (caps & {"lineage", "task_ops", "artifact_search"})

    def test_each_capability_has_transport(self):
        for spec in _load_manifest()["capabilities"].values():
            assert isinstance(spec.get("transport"), str) and spec["transport"]


# ============ BDPAdapter 能力声明 ============


def _probes(mcp: bool, dp: bool) -> dict[str, object]:
    return {"bdp-asset-mcp": lambda: mcp, "dp-cookie-http": lambda: dp}


class TestBdpAdapter:
    def test_name(self):
        assert BDPAdapter(probes=_probes(True, True)).name == "bdp"

    def test_both_transports_ready_full_capabilities(self):
        adapter = BDPAdapter(probes=_probes(True, True))
        assert adapter.capabilities() == frozenset(
            {Capability.TABLE_METADATA, Capability.SQL_EXECUTE, Capability.DQC_EXECUTE}
        )

    def test_mcp_only(self):
        adapter = BDPAdapter(probes=_probes(True, False))
        assert adapter.capabilities() == frozenset({Capability.TABLE_METADATA})

    def test_dp_only(self):
        adapter = BDPAdapter(probes=_probes(False, True))
        assert adapter.capabilities() == frozenset({Capability.SQL_EXECUTE, Capability.DQC_EXECUTE})

    def test_neither_ready_zero_capabilities(self):
        assert BDPAdapter(probes=_probes(False, False)).capabilities() == frozenset()

    def test_never_claims_unimplemented_verbs(self):
        adapter = BDPAdapter(probes=_probes(True, True))
        for cap in ("lineage", "task_ops", "artifact_search"):
            assert not adapter.has_capability(cap)

    def test_unknown_transport_fails_fast(self):
        manifest = {"platform": "bdp", "capabilities": {"sql_execute": {"transport": "pigeon"}}}
        with pytest.raises(Exception, match="pigeon"):
            BDPAdapter(manifest=manifest, probes={})

    def test_health_check_delegates_to_executor_when_sql_declared(self):
        adapter = BDPAdapter(probes=_probes(True, True))
        fake_result = MagicMock(success=True, data={"status": "ok"})
        with patch("src.aqueduct.tools.registry.get_tool") as get_tool:
            get_tool.return_value.execute.return_value = fake_result
            report = adapter.health_check()
        assert report["status"] == "ok"
        get_tool.assert_called_once_with("executor")

    def test_health_check_unavailable_without_sql(self):
        adapter = BDPAdapter(probes=_probes(True, False))
        assert adapter.health_check()["status"] == "unavailable"


# ============ loader 路由 ============


class TestLoaderBdpRouting:
    def test_bdp_mode_routes_to_bdp_adapter(self, monkeypatch):
        from src.aqueduct.platform import load_adapter
        from src.aqueduct.platform.bdp import BDPAdapter

        settings = get_settings()
        monkeypatch.setattr(settings, "platform", "bdp")
        adapter = load_adapter(settings)
        assert isinstance(adapter, BDPAdapter)

    def test_bdp_capability_set_matches_auto_when_probes_pass(self, monkeypatch):
        """兼容锚：探针通过时 bdp 与 auto 声明同集（auto 语义不变）。"""
        from src.aqueduct.platform import load_adapter

        settings = get_settings()
        monkeypatch.setattr(settings, "platform", "auto")
        monkeypatch.setattr("src.aqueduct.mcp.config.MCPConfig.is_configured", lambda self: True)
        auto = load_adapter(settings)
        monkeypatch.setattr(settings, "platform", "bdp")
        bdp = load_adapter(settings)
        assert bdp.capabilities() == auto.capabilities()


# ============ DP 凭证 .env 回退 ============


class TestDpEnvFallback:
    def test_env_file_fills_missing_keys(self, isolated_project_root, monkeypatch):
        (isolated_project_root / ".env").write_text(
            "DP_BASE_URL=https://dp.example.com\n"
            "# 注释行\n"
            "DP_COOKIE=BDPSESSION=tok=123\n"
            "DP_USER_ID=01234567\n"
            "OTHER=ignored\n",
            encoding="utf-8",
        )
        for k in ("DP_BASE_URL", "DP_COOKIE", "DP_USER_ID"):
            monkeypatch.delenv(k, raising=False)
        from src.aqueduct.mcp.adapters.dp_client import load_dp_env

        resolved = load_dp_env()
        assert resolved["DP_BASE_URL"] == "https://dp.example.com"
        assert resolved["DP_COOKIE"] == "BDPSESSION=tok=123"
        assert resolved["DP_USER_ID"] == "01234567"

    def test_os_environ_wins_over_env_file(self, isolated_project_root, monkeypatch):
        (isolated_project_root / ".env").write_text("DP_COOKIE=from-file\n", encoding="utf-8")
        monkeypatch.setenv("DP_COOKIE", "from-env")
        monkeypatch.delenv("DP_BASE_URL", raising=False)
        monkeypatch.delenv("DP_USER_ID", raising=False)
        from src.aqueduct.mcp.adapters.dp_client import load_dp_env

        assert load_dp_env()["DP_COOKIE"] == "from-env"

    def test_adapter_constructs_with_env_file_only(self, isolated_project_root, monkeypatch):
        """裸终端 aqueduct dev 场景：os.environ 无 DP_*，.env 有 → 可构造。"""
        (isolated_project_root / ".env").write_text(
            "DP_BASE_URL=https://dp.example.com\nDP_COOKIE=c\nDP_USER_ID=u\n",
            encoding="utf-8",
        )
        for k in ("DP_BASE_URL", "DP_COOKIE", "DP_USER_ID"):
            monkeypatch.delenv(k, raising=False)
        from src.aqueduct.mcp.adapters.dp_client import DataPlatformAdapter

        adapter = DataPlatformAdapter()
        assert adapter.base_url == "https://dp.example.com"

    def test_missing_everywhere_still_raises(self, isolated_project_root, monkeypatch):
        for k in ("DP_BASE_URL", "DP_COOKIE", "DP_USER_ID"):
            monkeypatch.delenv(k, raising=False)
        from src.aqueduct.mcp.adapters.dp_client import DataPlatformAdapter

        with pytest.raises(RuntimeError, match="DP_COOKIE"):
            DataPlatformAdapter()

    def test_fallback_does_not_pollute_os_environ(self, isolated_project_root, monkeypatch):
        (isolated_project_root / ".env").write_text("DP_COOKIE=from-file\n", encoding="utf-8")
        monkeypatch.delenv("DP_COOKIE", raising=False)
        from src.aqueduct.mcp.adapters.dp_client import load_dp_env

        load_dp_env()
        assert "DP_COOKIE" not in __import__("os").environ


# ============ DP 凭证第三层：bdp-cli session.json ============


def _write_session(
    home: Path,
    *,
    saved_at: str,
    env: str = "prod",
    cookie: str = "sess-cookie",
    user_id: str = "99998888",
    base_url: str = "https://sess.example.com",
) -> None:
    """在隔离 HOME 下写一份 bdp-cli 登录态文件。"""
    bdp = home / ".bdp"
    bdp.mkdir(parents=True, exist_ok=True)
    (bdp / "session.json").write_text(
        json.dumps(
            {
                "currentEnv": env,
                "sessions": {
                    env: {
                        "cookie": cookie,
                        "userId": user_id,
                        "baseUrl": base_url,
                        "savedAt": saved_at,
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """隔离 Path.home() 的两个环境变量来源（Windows USERPROFILE / POSIX HOME）。

    autouse：本文件所有测试不得看见真实 ~/.bdp/session.json（实录：
    test_missing_everywhere_still_raises 在有真实登录态的机器上被
    session.json 兜底击穿）。需要会话内容的测试再写 _write_session。
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    return home


class TestDpEnvSessionFallback:
    """load_dp_env 第三层：os.environ → .env → ~/.bdp/session.json。

    核心语义（2026-09-18 讨论）：session.json = bdp-cli 最后一次登录写入的
    最新鲜凭证——比 .env 新则胜出（忘跑 sync 也不挂）；比 .env 旧则让位
    （保留手动从浏览器拷 cookie 进 .env 的老流程）。
    """

    def test_session_fills_all_missing_keys(
        self, isolated_project_root, isolated_home, monkeypatch
    ):
        for k in ("DP_BASE_URL", "DP_COOKIE", "DP_USER_ID"):
            monkeypatch.delenv(k, raising=False)
        _write_session(isolated_home, saved_at="2026-09-18T03:54:28.254Z")
        from src.aqueduct.mcp.adapters.dp_client import load_dp_env

        resolved = load_dp_env()
        assert resolved["DP_COOKIE"] == "sess-cookie"
        assert resolved["DP_USER_ID"] == "99998888"
        assert resolved["DP_BASE_URL"] == "https://sess.example.com"

    def test_fresh_session_beats_stale_env_file(
        self, isolated_project_root, isolated_home, monkeypatch
    ):
        """忘同步场景：.env 里是旧 cookie、session.json 更新 → session 胜。"""
        from datetime import datetime, timezone

        (isolated_project_root / ".env").write_text(
            "DP_BASE_URL=https://dp.example.com\nDP_COOKIE=stale-cookie\nDP_USER_ID=01234567\n",
            encoding="utf-8",
        )
        stale = time.time() - 86400 * 3  # .env 三天前（旧 cookie 写入时刻）
        os.utime(isolated_project_root / ".env", (stale, stale))
        # session 比旧 .env 更新（动态 now：硬编码日期会在日期越过后被
        # now-3d 的 .env mtime 反超——2026-09-22 实录时间炸弹）
        fresh_saved_at = datetime.now(timezone.utc).isoformat()
        _write_session(isolated_home, saved_at=fresh_saved_at, cookie="fresh-cookie")
        for k in ("DP_BASE_URL", "DP_COOKIE", "DP_USER_ID"):
            monkeypatch.delenv(k, raising=False)
        from src.aqueduct.mcp.adapters.dp_client import load_dp_env

        resolved = load_dp_env()
        assert resolved["DP_COOKIE"] == "fresh-cookie"
        assert resolved["DP_USER_ID"] == "99998888"  # session 值一并胜出
        assert resolved["DP_BASE_URL"] == "https://dp.example.com"  # base_url 以 .env 显式配置为准

    def test_stale_session_loses_to_env_file(
        self, isolated_project_root, isolated_home, monkeypatch
    ):
        """手动拷 cookie 进 .env 的老流程：.env 比 session.json 新 → .env 胜。"""
        (isolated_project_root / ".env").write_text("DP_COOKIE=manual-fresh\n", encoding="utf-8")
        _write_session(isolated_home, saved_at="2020-01-01T00:00:00.000Z", cookie="old-sess")
        monkeypatch.delenv("DP_COOKIE", raising=False)
        monkeypatch.delenv("DP_BASE_URL", raising=False)
        monkeypatch.delenv("DP_USER_ID", raising=False)
        from src.aqueduct.mcp.adapters.dp_client import load_dp_env

        assert load_dp_env()["DP_COOKIE"] == "manual-fresh"

    def test_os_environ_still_wins_over_session(
        self, isolated_project_root, isolated_home, monkeypatch
    ):
        (isolated_project_root / ".env").write_text("DP_COOKIE=from-file\n", encoding="utf-8")
        _write_session(isolated_home, saved_at="2026-09-18T03:54:28.254Z", cookie="sess-cookie")
        monkeypatch.setenv("DP_COOKIE", "from-env")
        monkeypatch.delenv("DP_BASE_URL", raising=False)
        monkeypatch.delenv("DP_USER_ID", raising=False)
        from src.aqueduct.mcp.adapters.dp_client import load_dp_env

        assert load_dp_env()["DP_COOKIE"] == "from-env"

    def test_current_env_selects_session_section(
        self, isolated_project_root, isolated_home, monkeypatch
    ):
        _write_session(
            isolated_home, saved_at="2026-09-18T03:54:28.254Z", env="sit", cookie="sit-cookie"
        )
        for k in ("DP_BASE_URL", "DP_COOKIE", "DP_USER_ID"):
            monkeypatch.delenv(k, raising=False)
        from src.aqueduct.mcp.adapters.dp_client import load_dp_env

        assert load_dp_env()["DP_COOKIE"] == "sit-cookie"

    @pytest.mark.parametrize("content", [None, "{broken json"])
    def test_missing_or_broken_session_falls_back_to_env(
        self, isolated_project_root, isolated_home, monkeypatch, content
    ):
        (isolated_project_root / ".env").write_text("DP_COOKIE=from-file\n", encoding="utf-8")
        if content is not None:
            bdp = isolated_home / ".bdp"
            bdp.mkdir(parents=True)
            (bdp / "session.json").write_text(content, encoding="utf-8")
        monkeypatch.delenv("DP_COOKIE", raising=False)
        monkeypatch.delenv("DP_BASE_URL", raising=False)
        monkeypatch.delenv("DP_USER_ID", raising=False)
        from src.aqueduct.mcp.adapters.dp_client import load_dp_env

        assert load_dp_env()["DP_COOKIE"] == "from-file"

    def test_adapter_constructs_with_session_only(
        self, isolated_project_root, isolated_home, monkeypatch
    ):
        """bdp-cli 登录后零配置场景：不写 .env、不设环境变量也能构造适配器。"""
        _write_session(isolated_home, saved_at="2026-09-18T03:54:28.254Z")
        for k in ("DP_BASE_URL", "DP_COOKIE", "DP_USER_ID"):
            monkeypatch.delenv(k, raising=False)
        from src.aqueduct.mcp.adapters.dp_client import DataPlatformAdapter

        adapter = DataPlatformAdapter()
        assert adapter.base_url == "https://sess.example.com"
        assert adapter.cookie == "sess-cookie"
