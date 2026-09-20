"""MCP client 表结构查询测试。

覆盖第四刀 4b 的配套硬化（run 7 实录）：
  - MCP 错误文本（"MCP error -32602: ..."）不得伪装成"成功 0 字段"空结构
    ——run 7 全部 13 张表被解析成 0 字段还报成功，空结构冒充权威源表结构。
  - responseMapping 中文注释回退（comment 空时取 columnNameCN）。
  - search_first 两步流程（先搜表 ID 再查详情）端到端。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.aqueduct.mcp.client import MCPClient
from src.aqueduct.mcp.config import MCPConfig


def _make_client(tmp_path: Path, response_mapping: dict | None = None) -> MCPClient:
    """构造带合成 server 配置的底层客户端（哑进程，不真正 spawn 子进程）。"""
    config = {"mcpServers": {"test_server": {"command": "noop", "args": []}}}
    cfg_path = tmp_path / ".mcp.json"
    cfg_path.write_text(json.dumps(config), encoding="utf-8")
    client = MCPClient(config=MCPConfig(config_path=cfg_path), server_name="test_server")
    client._process = MagicMock()  # 哑进程：避免构造期 spawn
    client._initialized = True
    if response_mapping is not None:
        client._response_mapping = response_mapping
    return client


class TestMcpErrorSurfaces:
    """MCP 错误必须抛异常，不得静默解析成空结构（run 7 实录）。"""

    def test_error_text_raises_not_empty_schema(self, tmp_path):
        """content text 为 'MCP error -32602: ...' 时，解析应抛 RuntimeError。"""
        client = _make_client(tmp_path)
        poisoned = {
            "content": [
                {"type": "text", "text": "MCP error -32602: Tool get_table_schema not found"}
            ]
        }
        with pytest.raises(RuntimeError, match="-32602"):
            client._parse_table_schema(poisoned, "db", "tbl")

    def test_error_result_is_error_flag(self, tmp_path):
        """MCP result.isError=true 且文本为错误时同样抛异常。"""
        client = _make_client(tmp_path)
        poisoned = {
            "isError": True,
            "content": [{"type": "text", "text": "MCP error: something broke"}],
        }
        with pytest.raises(RuntimeError, match="something broke"):
            client._parse_table_schema(poisoned, "db", "tbl")


class TestCommentFallback:
    """responseMapping 中文注释回退：comment 为空时取 fallback 列。"""

    def test_comment_falls_back_to_cn_name(self, tmp_path):
        mapping = {
            "get_table_schema": {
                "columns_path": "data.columnList",
                "column_name_path": "columnName",
                "column_type_path": "columnType",
                "column_comment_path": "comment",
                "column_comment_fallback_path": "columnNameCN",
                "table_comment_path": "data.comment",
            }
        }
        client = _make_client(tmp_path, response_mapping=mapping)
        result = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "type": "hiveTableDetail",
                            "data": {
                                "comment": "运单明细表",
                                "columnList": [
                                    {
                                        "columnName": "waybill_no",
                                        "columnNameCN": "运单号",
                                        "columnType": "string",
                                        "comment": "",
                                    },
                                    {
                                        "columnName": "sign_time",
                                        "columnNameCN": "签收时间",
                                        "columnType": "string",
                                        "comment": "签收时间戳",
                                    },
                                ],
                            },
                        },
                        ensure_ascii=False,
                    ),
                }
            ]
        }
        schema = client._parse_table_schema(result, "dm_x", "dwd_t")
        assert schema.comment == "运单明细表"
        assert schema.columns[0].name == "waybill_no"
        assert schema.columns[0].comment == "运单号", "comment 为空时应回退到中文列名"
        assert schema.columns[1].comment == "签收时间戳", "comment 非空时优先 comment 本身"


class TestSearchFirstFlow:
    """search_first 两步流程端到端（mock 工具调用，不 spawn 子进程）。"""

    def test_two_step_search_then_detail(self, tmp_path):
        client = _make_client(tmp_path)
        client._tool_mapping = {
            "get_table_schema": {
                "name": "detail_tool",
                "search_first": {
                    "name": "search_tool",
                    "arguments": {"keywords": "$table", "size": 5},
                    "extract_id_path": "data.0.tblId",
                    "id_param_name": "id",
                },
            }
        }
        client._response_mapping = {
            "get_table_schema": {
                "columns_path": "data.columnList",
                "column_name_path": "columnName",
                "column_type_path": "columnType",
                "column_comment_path": "comment",
                "column_comment_fallback_path": "columnNameCN",
            }
        }

        calls: list[tuple[str, dict]] = []

        def fake_run(tool_name: str, arguments: dict) -> dict:
            calls.append((tool_name, arguments))
            if tool_name == "search_tool":
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "data": [
                                        {"tblId": "bdp_meta_123", "tblName": "dwd_t"},
                                        {"tblId": "bdp_meta_999", "tblName": "other"},
                                    ]
                                }
                            ),
                        }
                    ]
                }
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "data": {
                                    "columnList": [
                                        {
                                            "columnName": "waybill_no",
                                            "columnNameCN": "运单号",
                                            "columnType": "string",
                                            "comment": "",
                                        }
                                    ]
                                }
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]
            }

        client._run_mcp_tool = fake_run  # type: ignore[method-assign]
        schema = asyncio.run(client.get_table_schema("dm_x", "dwd_t"))

        assert [c[0] for c in calls] == ["search_tool", "detail_tool"]
        assert calls[0][1] == {"keywords": "dwd_t", "size": 5}
        assert calls[1][1] == {"id": "bdp_meta_123"}, "detail 应使用搜索命中的第一条 tblId"
        assert schema.columns[0].name == "waybill_no"
        assert schema.columns[0].comment == "运单号"

    def test_search_no_hit_raises(self, tmp_path):
        """搜索未命中（data 为空）时抛 TableNotFoundError，不得返回空结构。"""
        from src.aqueduct.mcp.tools import TableNotFoundError

        client = _make_client(tmp_path)
        client._tool_mapping = {
            "get_table_schema": {
                "name": "detail_tool",
                "search_first": {
                    "name": "search_tool",
                    "arguments": {"keywords": "$table"},
                    "extract_id_path": "data.0.tblId",
                },
            }
        }

        def fake_run(tool_name: str, arguments: dict) -> dict:
            return {"content": [{"type": "text", "text": json.dumps({"data": []})}]}

        client._run_mcp_tool = fake_run  # type: ignore[method-assign]
        with pytest.raises(TableNotFoundError):
            asyncio.run(client.get_table_schema("dm_x", "no_such_tbl"))
