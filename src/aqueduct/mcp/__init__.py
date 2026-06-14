"""MCP 层 — 模型上下文协议集成。

提供标准化的 MCP Client 接口，让用户可以连接自己的数据研发平台。

架构:
    aqueduct (固定代码)
      ↓ 读取 .mcp.json
    MCP Client (标准协议)
      ↓ 连接到...
    MCP Server (用户部署)
      ↓ 实际连接...
    用户的数据研发平台 (每个公司不同)
"""
