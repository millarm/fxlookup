# fx-rates

Phase 1 pipeline: fetch Bank of England FX rates → validate → generate Oracle FBDI-ready CSV/zip evidence pack.

## Overview

```
BoE IADB API  →  validate  →  evidence pack  →  GlDailyRatesInterface.zip
                  (variance)    (SHA-256)          (FBDI-ready)
```

Phase 1 terminates at `status=generated`. Oracle upload is Phase 2.

## Installation

```bash
pip install -e ".[dev]"
```

Requires Python 3.12+.

## CLI Usage

### Run the weekly pipeline

```bash
# Run for the current week
fx-rates run

# Run for a specific week (any date within the target week)
fx-rates run --week 2025-04-07

# Override a variance HOLD (>5% move) – does not override BLOCK (>10%)
fx-rates run --week 2025-04-07 --force

# Dry-run: fetch and preview without writing ledger or evidence
fx-rates run --dry-run
fx-rates run --week 2025-04-07 --dry-run

# Custom paths
fx-rates run --evidence-dir /data/evidence --ledger /data/ledger.db
```

### Show recent run history

```bash
fx-rates status
fx-rates status --limit 50 --ledger /data/ledger.db
```

### Replay a historical week

```bash
fx-rates replay 2025-01-13
fx-rates replay 2025-01-13 --force
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0    | Success – FBDI artefacts generated |
| 10   | Already-run – idempotency guard triggered |
| 20   | Variance HOLD – a pair moved >5%, use `--force` to override |
| 21   | Variance BLOCK – a pair moved >10%, hard block, no override |
| 30   | Source unavailable – BoE fetch or parse failed |

## Agent API

```python
from fx_rates.agent_api import run_weekly, dry_run, get_status
from datetime import date

# Full run
result = run_weekly(
    run_date=date(2025, 4, 7),
    force=False,
    evidence_dir="./evidence",
    ledger_path="./ledger.db",
)
print(result.exit_code)   # 0 = success
print(result.rates)        # {('GBP', 'USD'): Decimal('1.254300'), ...}
print(result.evidence_path)
print(result.manifest_sha256)

# Dry run (no side effects)
dry = dry_run(run_date=date(2025, 4, 7))
print(dry.csv_preview)

# Status
records = get_status(ledger_path="./ledger.db", limit=10)
for r in records:
    print(r.run_id, r.status, r.applied_from, r.applied_to)
```

## Date Logic

- **Applied window**: always a full Mon–Sun week.  `applied_from` = most recent Monday ≤ `run_date`.
- **Source date**: most recent BoE publication day strictly before `applied_from`.
  - BoE publishes Mon–Fri excluding England/Wales bank holidays.
  - `source_date_exception=True` when had to go back past the immediately preceding Friday (bank-holiday fallback).

### Examples

| Run date | Applied window | Source date | Exception? |
|----------|---------------|-------------|-----------|
| Mon 7 Apr 2025 | 7–13 Apr | Fri 4 Apr | No |
| Wed 9 Apr 2025 | 7–13 Apr | Fri 4 Apr | No |
| Mon 21 Apr 2025 (Easter Mon) | 21–27 Apr | Thu 17 Apr | **Yes** |
| Mon 7 Apr 2026 | 7–13 Apr | Wed 2 Apr | **Yes** |

## Currency Pairs

| BoE Series | Oracle Pair |
|-----------|------------|
| XUDLUSS | GBP/USD |
| XUDLERS | GBP/EUR |
| XUDLJYS | GBP/JPY |
| XUDLCDS | GBP/CAD |

## Evidence Pack

Each run writes to `{evidence_dir}/{run_id}/`:

```
boe_raw.csv              # exact bytes from BoE IADB response
boe_request.json         # url, timestamp, headers, http_status
rates_derived.json       # parsed rates with precision info
fbdi_upload.csv          # generated FBDI CSV
GlDailyRatesInterface.zip
manifest.json            # SHA-256 of every file + run metadata
```

The `manifest_sha256` (SHA-256 of `manifest.json` itself) is stored in the ledger.

## Variance Thresholds

| Change | Action |
|--------|--------|
| >2%    | Warning logged |
| >5%    | HOLD – exit 20, use `--force` to override |
| >10%   | BLOCK – exit 21, no override |

## FBDI Output Format

`GlDailyRatesInterface.csv` column layout:

```
From Currency, To Currency, User Conversion Type, From Conversion Date,
To Conversion Date, Conversion Rate, Inverse Conversion Rate, Mode Flag
```

- Dates: `DD/MM/YYYY`
- Rates: right-padded to exactly 6 decimal places, truncated (not rounded)
- Mode Flag: `Insert`
- Conversion Type: `Corporate`

## Running Tests

```bash
pytest
pytest tests/unit/test_dates.py -v
pytest tests/unit/test_csv_builder.py -v
```

## Project Structure

```
fx_rates/
  providers/
    base.py          # RateProvider protocol
    boe.py           # BoE IADB client
  oracle/
    csv_builder.py   # FBDI CSV + zip generation
    fbdi_client.py   # Phase 2 stub
  state/
    models.py        # Pydantic: RunRecord, RateRecord
    ledger.py        # SQLite run ledger
  policy/
    dates.py         # resolve_source_date(), resolve_applied_window()
    validation.py    # Variance thresholds, completeness checks
    holidays.py      # UK bank holiday cache (python-holidays)
  evidence/
    writer.py        # Evidence pack + SHA-256 manifest
  agent_api.py       # run_weekly(), dry_run(), get_status()
  cli.py             # Typer CLI
```
