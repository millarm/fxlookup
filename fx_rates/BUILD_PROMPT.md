Build the Phase 1 fx_rates Python package as described below. Create all files from scratch.

## SPEC SUMMARY

Phase 1: BoE fetch → validation → evidence pack → FBDI-ready CSV/zip. No Oracle upload in Phase 1.

## PACKAGE STRUCTURE TO BUILD

```
fx_rates/
  __init__.py
  providers/
    __init__.py
    base.py          # RateProvider protocol
    boe.py           # BoE IADB client (primary, Phase 1)
  oracle/
    __init__.py
    csv_builder.py   # GlDailyRatesInterface.csv + zip generation (Phase 1)
    fbdi_client.py   # STUB only - Phase 2, not implemented
  state/
    __init__.py
    models.py        # Pydantic models: RunRecord, RateRecord
    ledger.py        # SQLite run ledger
  policy/
    __init__.py
    dates.py         # resolve_source_date(), resolve_applied_window() - pure functions
    validation.py    # variance thresholds, completeness checks
    holidays.py      # UK bank holiday cache (python-holidays, England/Wales)
  evidence/
    __init__.py
    writer.py        # Evidence artefact writer + manifest + SHA-256
  agent_api.py       # Typed functions for agent invocation: run_weekly(), dry_run(), status()
  cli.py             # Typer CLI: run, dry-run, status, replay
pyproject.toml
README.md
tests/
  unit/
    test_dates.py    # Exhaustive: DST boundaries, bank holidays 2025-2030, late runs
    test_validation.py
    test_csv_builder.py
  fixtures/
    boe_responses/
      sample_response.csv   # A realistic fake BoE IADB CSV response
```

## KEY RULES

### Date logic (policy/dates.py)
- `resolve_applied_window(run_date)` returns tuple (applied_from: date, applied_to: date)
  - applied_from = most recent Monday <= run_date
  - applied_to = applied_from + 6 days (Sunday)
- `resolve_source_date(applied_from, holidays)` returns tuple (source_date: date, exception: bool)
  - source_date = most recent BoE publication day BEFORE applied_from
  - BoE publishes Mon-Fri excluding England/Wales bank holidays
  - exception=True if had to go back past Friday (i.e. bank holiday fallback triggered)
- These functions are PURE - no I/O, take only date + holiday set

### BoE IADB fetch (providers/boe.py)
- URL: https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp
- Params: csv.x=yes, Datefrom in DD/Mmm/YYYY format, Dateto in DD/Mmm/YYYY format, SeriesCodes=XUDLUSS,XUDLERS,XUDLJYS,XUDLCDS, CSVF=TN, UsingCodes=Y, VPD=Y, VFD=N
- Date window: source_date minus 3 days to source_date plus 3 days (tolerates holiday shifts)
- Validates: HTTP 200, CSV parseable, all 4 series present, row for exact source_date present, all values positive decimals, no NaN/null
- Returns dict mapping series code to Decimal rate value
- Series to Oracle pair mapping: XUDLUSS=GBP/USD, XUDLERS=GBP/EUR, XUDLJYS=GBP/JPY, XUDLCDS=GBP/CAD

### Run ledger (state/ledger.py)
- SQLite at configurable path, default: ledger.db in working directory
- Status values: pending, fetched, validated, generated, uploaded, reconciled, failed
- Phase 1 terminal success: generated
- Idempotency: if generated/uploaded/reconciled row exists for same (applied_from, applied_to), raise AlreadyRunError with exit code 10
- Schema:
  - runs table: run_id (ULID TEXT PK), run_date, source_date, applied_from, applied_to, status, evidence_path, manifest_sha256, source_date_exception (bool), created_at, updated_at
  - rates table: run_id, from_ccy, to_ccy, rate DECIMAL text, source_precision INT, PK(run_id, from_ccy, to_ccy)

### FBDI output (oracle/csv_builder.py)
- Columns: From Currency, To Currency, User Conversion Type, From Conversion Date, To Conversion Date, Conversion Rate, Inverse Conversion Rate, Mode Flag
- Values: GBP, USD/EUR/CAD/JPY, Corporate, Monday date DD/MM/YYYY, Sunday date DD/MM/YYYY, rate to 6dp, blank, Insert
- Rate precision: right-pad BoE value to exactly 6dp no rounding (e.g. 1.3449 becomes 1.344900)
- Output: GlDailyRatesInterface.csv then zipped to GlDailyRatesInterface.zip
- The zip must be Oracle FBDI-compatible (single CSV inside zip with correct filename)

### Evidence pack (evidence/writer.py)
- Writes to: {evidence_dir}/{run_id}/
- Files written in order:
  1. boe_raw.csv - exact bytes from BoE response
  2. boe_request.json - url, timestamp, response_headers, http_status
  3. rates_derived.json - parsed rate dict with precision info
  4. fbdi_upload.csv - the generated FBDI CSV
  5. GlDailyRatesInterface.zip - the zip
  6. manifest.json - SHA-256 of every file, run_id, timestamps, source_date, applied range, variance info
- Returns manifest_sha256 (SHA-256 of manifest.json itself)

### Variance check (policy/validation.py)
- Compare each pair rate to prior run rate for same pair
- Over 2%: log warning, continue
- Over 5%: raise VarianceHoldError (exit code 20) - caller can force to override
- Over 10%: raise VarianceBlockError (exit code 21) - hard block, no override
- If no prior run: skip variance checks, log fact

### agent_api.py
- run_weekly function: params run_date (optional date), force (bool default False), evidence_dir (optional path), ledger_path (optional path), returns RunResult
- dry_run function: params run_date (optional date), returns DryRunResult. Fetches and generates files but does NOT write to ledger or evidence
- get_status function: params ledger_path (optional path), returns list of RunRecord
- All return typed dataclasses, never raise directly to caller - catch and return error status
- RunResult exit_code values: 0=success, 10=already-run, 20=variance-hold, 21=variance-block, 30=source-unavailable, 40=oracle-failure(future)

### cli.py
- Typer app
- Commands: run with options --week YYYY-MM-DD, --force, --dry-run, --evidence-dir PATH, --ledger PATH
- Output: human-readable summary plus exit code
- On success: print rates table and file paths
- On variance hold: print which pairs breached and by how much, hint to use --force

### pyproject.toml
- name: fx-rates
- python >= 3.12
- dependencies: requests, pandas, python-holidays, ulid-py, typer, pydantic>=2

### tests/unit/test_dates.py - MUST include these cases:
- Normal Monday run gives Friday source date
- Tuesday late run still gets correct Monday-Sunday window
- Good Friday (UK bank holiday) falls back to Thursday
- Easter Monday week source date is Thursday before Easter
- 2026 Easter: Good Friday 3 Apr, Easter Monday 6 Apr, week of 7 Apr should use Wednesday 2 Apr
- 2027 Easter: Good Friday 26 Mar, Easter Monday 29 Mar, week of 30 Mar should use Thursday 25 Mar (only Friday is holiday)
- Check all UK bank holidays 2025-2030 don't cause errors

## IMPORTANT
- Use Decimal for all rate arithmetic (never float)
- Use python-ulid for ULIDs (import ulid; ulid.new())
- All file I/O errors must be caught and wrapped in descriptive exceptions
- The oracle/fbdi_client.py should be a clear stub with NotImplementedError and a docstring saying Phase 2
- Write a README.md with usage examples

When completely finished, run: openclaw system event --text "Done: fx_rates Phase 1 package built" --mode now
