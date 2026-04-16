#!/usr/bin/env python3
"""Backfill fx_rates weekly data from 2025-01-06 to most recent Monday."""
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, "/root/.openclaw/agents/felix/workspace/fx_rates")

from fx_rates.agent_api import run_weekly

EVIDENCE_DIR = "/root/.openclaw/agents/felix/workspace/fx_rates/evidence"
LEDGER_PATH  = "/root/.openclaw/agents/felix/workspace/fx_rates/ledger.db"

start = date(2025, 1, 6)
end   = date(2026, 4, 13)

weeks = []
d = start
while d <= end:
    weeks.append(d)
    d += timedelta(weeks=1)

print(f"Backfilling {len(weeks)} weeks: {weeks[0]} → {weeks[-1]}\n")

ok = skipped = failed = 0

for i, run_date in enumerate(weeks, 1):
    result = run_weekly(
        run_date=run_date,
        force=False,
        evidence_dir=EVIDENCE_DIR,
        ledger_path=LEDGER_PATH,
    )
    status = result.exit_code
    if status == 0:
        rates = {f"{pair[0]}/{pair[1]}": str(rate) for pair, rate in result.rates.items()}
        print(f"[{i:2}/{len(weeks)}] {run_date} ✅  source={result.source_date}  {rates}")
        ok += 1
    elif status == 10:
        print(f"[{i:2}/{len(weeks)}] {run_date} ⏭  already run, skipping")
        skipped += 1
    else:
        print(f"[{i:2}/{len(weeks)}] {run_date} ❌  exit_code={status}  {getattr(result, 'error', '')}")
        failed += 1

    time.sleep(1.0)  # be polite to BoE servers

print(f"\nDone. OK={ok}  skipped={skipped}  failed={failed}")
