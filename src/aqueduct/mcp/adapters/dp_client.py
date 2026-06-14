"""数据平台 HTTP 执行适配器。

核心逻辑：提交 SQL -> 轮询状态 -> 获取结果
通过环境变量 DP_BASE_URL / DP_COOKIE / DP_USER_ID 配置。
"""

from __future__ import annotations

import logging
import os
import random
import string
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class DataPlatformAdapter:
    """数据平台 SQL 执行适配器。"""

    def __init__(self) -> None:
        self.base_url = os.environ.get("DP_BASE_URL", "")
        self.cookie = os.environ.get("DP_COOKIE", "")
        self.user_id = os.environ.get("DP_USER_ID", "")

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

        self.client = httpx.Client(
            base_url=self.base_url,
            headers={"Cookie": self.cookie},
            timeout=60.0,
        )

    def _generate_window_id(self) -> str:
        return f"copilot_{''.join(random.choices(string.ascii_lowercase + string.digits, k=8))}"

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
        result_id = self._hive_wait(exec_id)
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
        endpoint = "/data-platform-api/hive/execute"
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
        if res_data.get("code") != 200:
            raise RuntimeError(f"提交失败: {res_data}")
        return int(res_data["data"]["executionId"])

    def _hive_wait(self, execution_id: int) -> int:
        # 轮询检查状态
        for _attempt in range(60):  # Max 5 minutes
            time.sleep(5)
            endpoint = "/data-platform-api/hive/executionStatus"
            payload = {"executionId": execution_id, "userId": self.user_id}
            resp = self.client.post(endpoint, json=payload)
            resp.raise_for_status()
            res_data = resp.json()

            status = res_data["data"]["status"]
            if status == 3:  # Success
                return int(res_data["data"]["resultId"])
            elif status in [4, 5]:  # Failed
                raise RuntimeError(f"任务执行失败: {res_data}")

        raise TimeoutError("任务超时 (5 min)")

    def _hive_fetch(self, result_id: int, window_id: str) -> list[dict[str, Any]]:
        endpoint = "/data-platform-api/hive/result"
        # 默认取前 1000 行，防止数据量过大
        payload = {
            "resultId": result_id,
            "userId": self.user_id,
            "windowId": window_id,
            "limit": 1000,
        }
        resp = self.client.post(endpoint, json=payload)
        resp.raise_for_status()
        res_data = resp.json()

        if res_data.get("code") != 200:
            raise RuntimeError(f"获取结果失败: {res_data}")

        return res_data["data"].get("records", [])
