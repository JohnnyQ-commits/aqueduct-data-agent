"""P1-2 冒烟：v5 真实 ETL SQL 过试跑门禁全链路（提取 → health → LIMIT 10 真跑）。

DataPlatformAdapter 读裸 os.environ（.env 只进 pydantic Settings）——
真实 CLI 场景外需手动注入；只注入不打印（凭证脱敏）。
"""

import sys

from dotenv import load_dotenv

sys.path.insert(0, "src")
load_dotenv(r"E:\test\aqueduct\.env")

from aqueduct.engine.nodes.sql import _extract_select_statements, _run_trial_selects
from aqueduct.tools.registry import get_tool

sql = open(
    r"E:\test\aqueduct\output\ecommerce_daily_stat_v5\Phase4-ecommerce_daily_stat.sql",
    encoding="utf-8",
).read()

out = []
stmts = _extract_select_statements(sql)
out.append(f"[1] extractor: {len(stmts)} 条可试跑语句（P1-2 修复前=0，INSERT 头全跳过）")
for i, s in enumerate(stmts, 1):
    out.append(f"    stmt#{i} head: {s[:70]!r}")

executor = get_tool("executor")
health = executor.execute(action="health_check")
out.append(f"[2] health_check: success={health.success}, data={health.data}")

if health.success:
    result = _run_trial_selects(sql)
    out.append(
        f"[3] trial: total={result['total']} tested={result['tested']} "
        f"passed={result['passed']} failed={len(result['errors'])}"
    )
    for e in result["errors"]:
        out.append(f"    ERROR: {e[:300]}")
    if not result["errors"]:
        out.append("    ✅ 门禁判定：通过（无 Critical 注入）")
else:
    out.append("[3] 平台不可达，跳过试跑（防护链正确行为）")

sys.stdout.buffer.write(("\n".join(out) + "\n").encode("utf-8"))
