"""数据平台 HTTP 执行适配器。

核心逻辑：提交 SQL -> 轮询状态 -> 获取结果
通过环境变量 DP_BASE_URL / DP_COOKIE / DP_USER_ID 配置。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import string
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DP_ENV_KEYS = ("DP_BASE_URL", "DP_COOKIE", "DP_USER_ID")


def _read_bdp_session() -> dict[str, str]:
    """读取 bdp-cli 登录态文件（~/.bdp/session.json）里的平台凭证。

    第三层凭证源（os.environ / 项目 .env 之后）：bdp-cli login 弹窗登录后
    落盘的最新凭证，管道零配置可跑（不再依赖 sync_bdp_session.py 的手动
    同步步骤）。按 currentEnv 选会话（缺省 prod）；文件缺失/损坏/无会话
    一律静默返回空 dict，由调用方按缺失处理。只读，不写回 os.environ。
    """
    session_path = Path.home() / ".bdp" / "session.json"
    try:
        data = json.loads(session_path.read_text(encoding="utf-8"))
        env_name = data.get("currentEnv") or "prod"
        section = (data.get("sessions") or {}).get(env_name) or {}
        result: dict[str, str] = {}
        if (section.get("cookie") or "").strip():
            result["DP_COOKIE"] = section["cookie"].strip()
        if str(section.get("userId") or "").strip():
            result["DP_USER_ID"] = str(section["userId"]).strip()
        if (section.get("baseUrl") or "").strip():
            result["DP_BASE_URL"] = section["baseUrl"].strip()
        result["_saved_at"] = str(section.get("savedAt") or "").strip()
        result["_file_mtime"] = str(session_path.stat().st_mtime)
        return result
    except Exception:
        return {}


def _session_fresher_than_env_file(saved_at: str, file_mtime: str, env_path: Path) -> bool:
    """session.json 凭证是否比项目 .env 更新（决定 DP_COOKIE/DP_USER_ID 归属）。

    双向防御：session 更新 → 忘同步也用新 cookie；.env 更新（手动从浏览器
    拷贝新 cookie 的老流程）→ 保留 .env。savedAt 解析失败回退 session 文件
    mtime；.env 不存在或时间不可知时视为 session 更新。
    """
    try:
        env_mtime = env_path.stat().st_mtime
    except OSError:
        return True
    session_time: float | None
    try:
        session_time = datetime.fromisoformat(saved_at.replace("Z", "+00:00")).timestamp()
    except Exception:
        try:
            session_time = float(file_mtime)
        except (TypeError, ValueError):
            return True
    return session_time >= env_mtime


def load_dp_env() -> dict[str, str]:
    """解析 DP_* 配置，三层凭证源按新鲜度归位：os.environ → .env → session.json。

    DP_BASE_URL / DP_COOKIE / DP_USER_ID 依次取 os.environ；缺失回退项目
    .env（CLI 管道模式 .env 不注入 os.environ，此前裸终端 ``aqueduct dev``
    门禁放行但执行时凭证缺失，两层口径不一致）；仍缺失或 session.json 更新
    时回退 bdp-cli 登录态。cookie/userId 的归属规则：os.environ 恒胜；
    .env 与 session.json 之间按时间新者胜（session = bdp-cli 最后一次登录
    写入，忘跑 sync 也用新 cookie；手动更新 .env 则保留手动值）；
    DP_BASE_URL 以显式配置（.env）为准，session 只在完全缺失时补位。
    值不写回 os.environ（不污染子进程），仅作本次解析结果返回。
    """
    env = {k: os.environ.get(k, "") for k in _DP_ENV_KEYS}
    source = {k: ("os" if env[k] else "") for k in _DP_ENV_KEYS}

    try:
        from ...config.settings import get_settings

        env_path = get_settings().project_root / ".env"
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        env_path = None
        lines = []
    parsed: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # cookie 值本身含 '='，只按第一个 '=' 切分；同键以首个出现为准
        if key in _DP_ENV_KEYS and key not in parsed:
            parsed[key] = value.strip()
    for k in _DP_ENV_KEYS:
        if not env[k] and parsed.get(k):
            env[k] = parsed[k]
            source[k] = "env_file"

    session = _read_bdp_session()
    if session:
        for k in ("DP_COOKIE", "DP_USER_ID"):
            if not session.get(k):
                continue
            if source[k] == "" or (
                source[k] == "env_file"
                and _session_fresher_than_env_file(
                    session["_saved_at"], session["_file_mtime"], env_path
                )
            ):
                env[k] = session[k]
        if not env["DP_BASE_URL"] and session.get("DP_BASE_URL"):
            env["DP_BASE_URL"] = session["DP_BASE_URL"]
    return env


class DataPlatformAdapter:
    """数据平台 SQL 执行适配器。"""

    def __init__(self) -> None:
        resolved = load_dp_env()
        self.base_url = resolved["DP_BASE_URL"]
        self.cookie = resolved["DP_COOKIE"]
        self.user_id = resolved["DP_USER_ID"]

        missing = []
        if not self.base_url:
            missing.append("DP_BASE_URL")
        if not self.cookie:
            missing.append("DP_COOKIE")
        if not self.user_id:
            missing.append("DP_USER_ID")
        if missing:
            raise RuntimeError(
                f"数据平台适配器缺少必要环境变量: {', '.join(missing)}。"
                f"请在系统环境变量、项目 .env 中配置，或执行 bdp-cli login 完成登录"
                f"（凭证自动取自 ~/.bdp/session.json）。"
            )

        # 安全检查：Cookie 不应通过明文 HTTP 传输
        if not self.base_url.startswith("https://"):
            logger.warning(
                "DP_BASE_URL 未使用 HTTPS，Cookie 凭证可能明文传输: %s",
                self.base_url,
            )

        self.client = httpx.Client(
            base_url=self.base_url,
            headers={"Cookie": self.cookie},
            timeout=60.0,
        )

    def __repr__(self) -> str:
        """脱敏表示，防止凭证泄露到日志/异常。"""
        return (
            f"DataPlatformAdapter(base_url={self.base_url!r}, cookie=***, user_id={self.user_id!r})"
        )

    def _generate_window_id(self) -> str:
        return f"copilot_{''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))}"

    @staticmethod
    def _envelope_ok(res_data: dict[str, Any]) -> bool:
        """平台信封双形态兼容：新 {ok: True, ...} / 旧 {code: 200, ...}。

        2026-09-12 冒烟发现 execute 端点已返回 ok 信封，硬编码 code 判断
        会把真实成功的提交判成"提交失败"（执行链路假死的第二层原因）。
        """
        if "ok" in res_data:
            return res_data["ok"] is True
        return res_data.get("code") == 200

    def execute_hive_query(self, sql: str) -> dict[str, Any]:
        """执行 Hive SQL 查询（提交 -> 轮询 -> 取结果）。"""
        sql_clean = sql.rstrip().rstrip(";").rstrip()
        window_id = self._generate_window_id()

        # 1. Submit
        logger.info(f"【步骤1】提交 Hive 任务... SQL: {sql_clean[:50]}...")
        exec_id = self._hive_submit(sql_clean, window_id)
        logger.info(f"【步骤1】任务已提交，executionId: {exec_id}")

        # 2. Wait
        logger.info("【步骤2】轮询任务状态...")
        result_id = self._hive_wait(exec_id, window_id)
        logger.info(f"【步骤2】任务完成，resultId: {result_id}")

        # 3. Fetch
        # 检查是否为 DDL (CREATE/DROP/ALTER)
        is_ddl = any(
            sql_clean.upper().startswith(kw) for kw in ["CREATE", "DROP", "ALTER", "TRUNCATE"]
        )

        if is_ddl:
            return {"status": "success", "data": [], "row_count": 0}

        logger.info("【步骤3】获取结果...")
        data = self._hive_fetch(result_id, window_id)
        logger.info(f"【步骤3】成功获取 {len(data)} 行数据")

        return {
            "status": "success",
            "data": data,
            "row_count": len(data) if data else 0,
        }

    def _hive_submit(self, sql: str, window_id: str) -> int:
        endpoint = "/bdp-fc-ide-external-controller/hive/execute"
        payload = {
            "applicationId": 624,
            "async": True,
            "clusterId": 1,
            "statement": sql,
            "mode": 2,
            "userId": self.user_id,
            "windowId": window_id,
        }
        resp = self.client.post(endpoint, json=payload)
        resp.raise_for_status()
        res_data = resp.json()
        if not self._envelope_ok(res_data):
            raise RuntimeError(f"提交失败: {res_data}")
        data = res_data.get("data")
        # 新信封 data 为裸 executionId int；旧信封为 {executionId: ...} 字典
        if isinstance(data, dict) and "executionId" in data:
            return int(data["executionId"])
        return int(data)

    def _hive_wait(self, execution_id: int, window_id: str) -> str:
        """轮询 getLog 直到完成，返回 resultId（UUID 字符串）。

        2026-09-14 全链路实测修正：文档写的 POST /hive/executionStatus 404，
        真实轮询端点是 GET /hive/getLog（isFinish/isSuccess/resultId），
        resultId 为 UUID 而非 int。
        """
        for _attempt in range(60):  # Max 5 minutes
            time.sleep(5)
            endpoint = "/bdp-fc-ide-external-controller/hive/getLog"
            resp = self.client.get(
                endpoint,
                params={
                    "clusterId": 1,
                    "windowId": window_id,
                    "executionId": execution_id,
                },
            )
            resp.raise_for_status()
            data = resp.json().get("data") or {}

            if data.get("isFinish"):
                if data.get("isSuccess"):
                    return str(data["resultId"])
                raise RuntimeError(f"任务执行失败: {data}")

        raise TimeoutError("任务超时 (5 min)")

    def _hive_fetch(self, result_id: str, window_id: str) -> list[dict[str, Any]]:
        """按 resultId 取结果行。

        真实端点 GET /hive/getResult，参数 resultId+windowId+clusterId——
        **不能带 userId**（live 实测：带则 500）。
        """
        endpoint = "/bdp-fc-ide-external-controller/hive/getResult"
        resp = self.client.get(
            endpoint,
            params={"resultId": result_id, "windowId": window_id, "clusterId": 1},
        )
        resp.raise_for_status()
        res_data = resp.json()

        if not self._envelope_ok(res_data):
            raise RuntimeError(f"获取结果失败: {res_data}")

        return res_data.get("data") or []
