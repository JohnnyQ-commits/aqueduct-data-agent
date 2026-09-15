"""数据平台 HTTP 执行适配器。

核心逻辑：提交 SQL -> 轮询状态 -> 获取结果
通过环境变量 DP_BASE_URL / DP_COOKIE / DP_USER_ID 配置。
"""

from __future__ import annotations

import logging
import os
import secrets
import string
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DP_ENV_KEYS = ("DP_BASE_URL", "DP_COOKIE", "DP_USER_ID")


def load_dp_env() -> dict[str, str]:
    """解析 DP_* 配置：os.environ 优先，缺失键回退项目 .env。

    CLI 管道模式 .env 不注入 os.environ（只有插件模式由 Claude Code 自动
    加载）——此前裸终端 ``aqueduct dev`` 门禁放行（Settings 读得到 .env 的
    execution_enabled）但执行时凭证缺失，两层口径不一致。值不写回
    os.environ（不污染子进程），仅作本次解析结果返回。
    """
    env = {k: os.environ.get(k, "") for k in _DP_ENV_KEYS}
    missing = [k for k, v in env.items() if not v]
    if not missing:
        return env
    try:
        from ...config.settings import get_settings

        env_path = get_settings().project_root / ".env"
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return env
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
    for k in missing:
        if parsed.get(k):
            env[k] = parsed[k]
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
                f"请在 .env 文件或系统环境变量中配置。"
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
