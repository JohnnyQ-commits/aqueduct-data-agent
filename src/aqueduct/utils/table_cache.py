"""表结构缓存 — 24 小时 TTL 避免重复 MCP 查询。

同一张表的结构在短期内不会变化，缓存查询结果可以避免重复
MCP 调用，显著减少管道执行时间（每次 MCP 查询约 2-5 秒）。

用法:
    cache = TableSchemaCache(ttl_seconds=86400)  # 24h TTL
    text = cache.get("db.schema.table")
    if text is None:
        text = query_from_mcp(...)
        cache.set("db.schema.table", text)
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class TableSchemaCache:
    """表结构 TTL 缓存。

    支持内存缓存 + 可选的磁盘持久化（跨进程复用）。
    """

    def __init__(
        self,
        ttl_seconds: int = 86400,
        persist_path: Path | None = None,
    ) -> None:
        """初始化缓存。

        Args:
            ttl_seconds: 缓存条目过期时间（秒），默认 24 小时。
            persist_path: 磁盘持久化路径（JSON 文件）。
                         为 None 时仅做内存缓存。
        """
        self._ttl = ttl_seconds
        self._persist_path = persist_path
        self._cache: dict[str, tuple[float, str]] = {}  # key → (timestamp, value)

        if persist_path:
            self._load_from_disk()

    def get(self, table_name: str) -> str | None:
        """查询缓存，命中且未过期时返回文本，否则返回 None。"""
        entry = self._cache.get(table_name)
        if entry is None:
            return None

        ts, value = entry
        age = time.time() - ts
        if age > self._ttl:
            del self._cache[table_name]
            logger.debug("表结构缓存过期: %s (age=%.0fs > ttl=%ds)", table_name, age, self._ttl)
            return None

        logger.debug("表结构缓存命中: %s (age=%.0fs)", table_name, age)
        return value

    def set(self, table_name: str, schema_text: str) -> None:
        """写入缓存条目。"""
        self._cache[table_name] = (time.time(), schema_text)

        if self._persist_path:
            self._save_to_disk()

    def get_many(self, table_names: list[str]) -> dict[str, str]:
        """批量查询，返回所有命中且未过期的条目。"""
        result: dict[str, str] = {}
        for name in table_names:
            value = self.get(name)
            if value is not None:
                result[name] = value
        return result

    def set_many(self, schemas: dict[str, str]) -> None:
        """批量写入缓存。"""
        now = time.time()
        for name, text in schemas.items():
            self._cache[name] = (now, text)

        if self._persist_path:
            self._save_to_disk()

    def invalidate(self, table_name: str) -> None:
        """主动失效某条缓存。"""
        self._cache.pop(table_name, None)
        if self._persist_path:
            self._save_to_disk()

    def clear(self) -> None:
        """清空所有缓存。"""
        self._cache.clear()
        if self._persist_path:
            self._save_to_disk()

    @property
    def size(self) -> int:
        """当前缓存条目数（含可能已过期但未清理的）。"""
        return len(self._cache)

    def stats(self) -> dict[str, Any]:
        """返回缓存统计信息。"""
        now = time.time()
        active = sum(1 for ts, _ in self._cache.values() if now - ts <= self._ttl)
        expired = len(self._cache) - active
        return {
            "total": len(self._cache),
            "active": active,
            "expired": expired,
            "ttl_seconds": self._ttl,
        }

    # ── 磁盘持久化 ────────────────────────────────────────────────────────────

    def _load_from_disk(self) -> None:
        """从磁盘加载缓存（跳过已过期条目）。"""
        if not self._persist_path or not self._persist_path.exists():
            return

        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            now = time.time()
            for key, (ts, value) in data.items():
                if now - ts <= self._ttl:
                    self._cache[key] = (ts, value)
            logger.info("从磁盘加载表结构缓存: %d 条有效 / %d 条总计", len(self._cache), len(data))
        except Exception as e:
            logger.warning("加载表结构缓存失败: %s", e)

    def _save_to_disk(self) -> None:
        """将当前缓存写入磁盘。"""
        if not self._persist_path:
            return

        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            # 只保存未过期的条目
            now = time.time()
            active = {k: v for k, v in self._cache.items() if now - v[0] <= self._ttl}
            self._persist_path.write_text(
                json.dumps(active, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("保存表结构缓存失败: %s", e)
