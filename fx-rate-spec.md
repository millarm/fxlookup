# Design Document

## Automated Weekly FX Rate Population for Oracle Fusion using Bank of England Daily Spot Rates

Version: 2.2 (Phase 1: file generation only; Oracle integration deferred to Phase 2)
Supersedes: v2.0 (generic Python deployment model)  
Supersedes: v1.0 (Oanda scraping design — withdrawn; licensing non-compliant)

---

## 1. Objective

Implement a controlled, auditable, API-driven Python process to populate Oracle Fusion Daily Rates (Corporate rate) weekly, using Bank of England published daily spot rates as the source. Core requirements:

- GBP as fixed base currency
- Rates applied Monday–Sunday
- Deterministic date logic, non-overlapping ranges
- No inverse-rate maintenance
- Source is free, licensed for redistribution, and central-bank published
- Reproducible, replayable, and safe for agent-managed invocation
- Implemented as a Felix skill invoked via OpenClaw cron and conversational approval

### 1.1 Phased Delivery

| Phase | Scope | Status |
|---|---|---|
| **Phase 1** | BoE fetch → validation → evidence pack → FBDI-ready CSV/zip | **In scope now** |
| **Phase 2** | Oracle UCM upload → ESS submit/poll → reconciliation | Deferred |

Phase 1 output is fully Oracle-compatible: the generated `GlDailyRatesInterface.zip` can be uploaded to Oracle Fusion manually (ADFdi or UCM) without modification. The design is structured so Phase 2 adds the upload client without changing any upstream logic.

## 2. Business Rules

| Item | Rule |
|------|------|
| Base Currency | GBP only |
| Currencies Required | GBP/USD, GBP/EUR, GBP/CAD, GBP/JPY |
| Rate Type | Corporate |
| Frequency | Weekly |
| Source Rate Date | Friday prior to week start (last BoE publication day) |
| Applied Period | Monday–Sunday (7 days, non-overlapping) |
| Inverse Rate | Not populated |
| Overlap | Disallowed |
| Audit Source | Bank of England Interactive Database (IADB) |

**Change from v1:** Source date moved from Sunday (an artefact of 24/7 FX markets) to Friday (the last day BoE publishes). Applied window shifted to Monday–Sunday to align with the working-week logic of the source. This is a stronger, not weaker, audit posture: you are now using the central bank's last published rate of the week, applied for the following business week.

**Example (Week of 20 Apr 2026):**
- Source FX date: Friday 17/04/2026
- Applied date range: 20/04/2026 – 26/04/2026
- One rate per currency pair

**Bank holiday handling:** If the preceding Friday is a UK bank holiday (no BoE publication), fall back to the most recent prior publication day (typically Thursday). This is logged as an exception in the run ledger but does not require human intervention.

---

## 3. Oracle Fusion Target Structure

### 3.1 FBDI Field Mapping

| Oracle Field | Value |
|---|---|
| From Currency | GBP |
| To Currency | USD / EUR / CAD / JPY |
| User Conversion Type | Corporate |
| From Conversion Date | Monday |
| To Conversion Date | Sunday (Monday + 6) |
| Conversion Rate | Rate (6dp) |
| Inverse Conversion Rate | Blank |
| ModeFlag | Insert |

### 3.2 Upload Mechanism

**Decision: FBDI via ErpIntegrationService REST** — not ADFdi.

An agent-managed solution needs a headless, loggable upload path. ADFdi remains the documented manual fallback if FBDI is unavailable.

---

## 4. Source System — Bank of England

### 4.1 Publication Characteristics

- **Publisher:** Bank of England, Statistics and Regulatory Data Division
- **Dataset:** Daily spot exchange rates against Sterling
- **Publication days:** London working days only (no weekends, no UK bank holidays)
- **Publication time:** Approximately 16:30 UK time (4pm London fix basis)
- **Methodology:** Mid-market rates, drawn from commercial market sources
- **Licence:** Bank of England Database Terms and Conditions permit redistribution for non-commercial and internal business purposes (confirm specific clause in audit memo before go-live)

### 4.2 Series Codes (confirmed)

| Pair | BoE Series Code | Description |
|---|---|---|
| GBP/USD | XUDLUSS | US dollar into Sterling, spot |
| GBP/EUR | XUDLERS | Euro into Sterling, spot |
| GBP/JPY | XUDLJYS | Japanese yen into Sterling, spot |
| GBP/CAD | XUDLCDS | Canadian dollar into Sterling, spot |

All four are native GBP-based, so no cross-rate arithmetic is required. This is a material simplification versus ECB (which is EUR-based) and a real improvement over the Oanda design.

### 4.3 Data Access — IADB CSV Endpoint

The Bank of England Interactive Database exposes a stable URL for CSV extraction:

```
https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp
  ?csv.x=yes
  &Datefrom={DD/Mmm/YYYY}
  &Dateto={DD/Mmm/YYYY}
  &SeriesCodes=XUDLUSS,XUDLERS,XUDLJYS,XUDLCDS
  &CSVF=TN
  &UsingCodes=Y
  &VPD=Y
  &VFD=N
```

Returns comma-separated data with a header row and one row per publication date. This endpoint has been stable for over a decade and is the documented programmatic access path. No authentication, no API key.

### 4.4 Source Disclaimer (Required Audit Quote)

> "The exchange rates are not official rates and are no more authoritative than that of any commercial bank operating in the London foreign exchange market."

This is standard BoE language; it does not disqualify use for corporate rate purposes but must be disclosed in the audit memo.

---

## 5. Automation Architecture

### 5.1 OpenClaw Integration Model

This process runs entirely within Felix (OpenClaw agent). There is no separate deployment:

- **Scheduler:** OpenClaw cron job (Monday ~09:00 London, after BoE 16:30 Friday publication)
- **Orchestrator:** Felix agent session invokes `agent_api.py` functions directly
- **Secrets:** Oracle credentials stored in OpenClaw secrets config; accessed via environment at runtime
- **Alerts:** Felix sends Telegram messages for variance holds, failures, and reconciliation mismatches
- **Approval workflow:** Variance >5% triggers a Telegram message with approve/reject; Felix waits for reply before proceeding
- **Run ledger:** SQLite file in Felix workspace (`workspace/fx_rates/ledger.db`)
- **Evidence store:** Felix workspace (`workspace/fx_rates/evidence/{run_id}/`); optionally synced to blob store if configured
- **Reconciliation:** Second OpenClaw cron job (Tuesday ~09:00)

### 5.2 Phase 1 Pipeline

```
OpenClaw cron (Monday AM)
 │
 ▼
 Felix agent_api.run_weekly()
 │
 ▼
 Date Logic Engine ──► resolve_source_date(run_date)  [pure, no I/O]
 │
 ▼
 Idempotency check ──► ledger.db: abort if already generated
 │
 ▼
 BoE IADB Client ──► fetch CSV, validate, parse
 │
 ▼
 Evidence Writer ──► workspace/fx_rates/evidence/{run_id}/
 │                     boe_raw.csv, boe_request.json
 ▼
 Variance Gate ──► >5%: Felix sends Telegram approval request
 │               >10%: hard fail, Felix alerts, stops
 ▼
 FBDI CSV Generator ──► GlDailyRatesInterface.csv
 │
 ▼
 Zip ──► GlDailyRatesInterface.zip  [← upload this to Oracle]
 │
 ▼
 Evidence Writer (final) ──► fbdi_upload.csv, manifest.json
 │
 ▼
 Run Ledger Update ──► ledger.db: status=generated
 │
 ▼
 Felix notifies ──► Telegram: ✅ files ready + workspace path
                              rates summary, source date, applied range
```

### 5.3 Phase 2 Pipeline (deferred)

```
[continues from Phase 1 'generated' state]
 │
 ▼
 Oracle FBDI Client ──► UCM upload → ESS submit → poll
 │
 ▼
 Run Ledger Update ──► status=uploaded/reconciled
 │
 ▼
 Felix notifies + Tuesday reconciliation cron
```

---

## 6. Date Logic (Core Control)

### 6.1 Week Determination

```
Given run_date (typically Monday):
  1. applied_from = most recent Monday ≤ run_date
  2. applied_to   = applied_from + 6 days (Sunday)
  3. source_date  = most recent BoE publication day < applied_from
                  = typically the preceding Friday
                  = Thursday if Friday was a UK bank holiday
                  = Wednesday if Thu+Fri both bank holidays (e.g. Easter)
```

### 6.2 Properties

- **Deterministic and pure (no I/O):** same run_date always produces same source_date and applied range; the function takes only a date and a holiday calendar, performs no network or disk I/O
- **Exhaustively unit-tested:** including DST boundaries and every UK bank holiday in the next 5 years
- **Re-runnable:** given the same run_date, produces identical source_date and applied_from/to
- **Resilient to late runs:** if triggered Tuesday instead of Monday, still resolves to the correct week (applied_from steps back to the most recent Monday ≤ run_date)

### 6.3 UK Bank Holiday Source

Use `python-holidays` with the UK calendar, specifically the **England and Wales** subdivision for BoE alignment. Cache the holiday list locally with a quarterly refresh job; never rely on a live fetch within the critical path.

### 6.4 Control Rules

1. **Never re-load an already-loaded week.** Before writing, query the run ledger for existing `uploaded` or `reconciled` rows for the same `(applied_from, applied_to)`. If found, abort with a clear message and exit code 1.
2. **Backfill is explicit only.** The process does not auto-backfill missed weeks. A backfill run requires an explicit `--week YYYY-MM-DD` parameter.
3. **Rate must exist.** If BoE returns no value for a series on the source date (e.g. data delay), the run fails loudly with exit code 2. Do not substitute with stale rates silently.

---

## 7. FX Rate Extraction Logic

### 7.1 Fetch Strategy

Single HTTP GET to the IADB endpoint with all four series codes and a narrow date window (`source_date ± 3 days`), to tolerate bank holiday shifts and validate that the expected date is present in the response.

### 7.2 Validation Rules (pre-transformation)

All must pass before proceeding:

- HTTP 200 response
- CSV parseable, header present, expected columns
- All four series codes present in response
- Row for exact `source_date` present and non-empty
- All four rate values parseable as positive decimals
- No NaN, null, or placeholder values

### 7.3 Precision

- BoE publishes to varying precision (typically 4dp for major pairs, more for JPY)
- Store and upload to 6 decimal places
- No internal rounding — take the BoE-published value, right-pad with zeros to 6dp, and upload verbatim
- Record original precision in the evidence manifest

---

## 8. Transformation & Validation

### 8.1 Data Validation (post-fetch)

- All 4 pairs present for the target date ✓
- Rate > 0 for all pairs ✓
- Week-on-week variance per pair within tolerance (see §8.3) ✓
- Row count exactly 4 in generated output ✓

### 8.2 Idempotency Control

Run ledger (SQLite, local to the deployment — Postgres if horizontally scaled) records:

```sql
runs (
  run_id         ULID PRIMARY KEY,
  run_date       DATE,
  source_date    DATE,
  applied_from   DATE,
  applied_to     DATE,
  status         ENUM('pending','fetched','validated','uploaded','reconciled','failed'),
  ess_request_id TEXT,
  evidence_path  TEXT,
  manifest_sha256 TEXT,
  created_at     TIMESTAMP,
  updated_at     TIMESTAMP
)

rates (
  run_id           ULID,
  from_ccy         TEXT,
  to_ccy           TEXT,
  rate             DECIMAL(18,6),
  source_precision INT,
  PRIMARY KEY (run_id, from_ccy, to_ccy)
)
```

The run ledger status enum reflects the phased model:

```sql
status ENUM('pending','fetched','validated','generated','uploaded','reconciled','failed')
```

- Phase 1 terminal success state: `generated`
- Phase 2 adds: `uploaded`, `reconciled`

Phase 2 picks up from any run in `generated` state, so manual uploads performed during Phase 1 can be recorded retroactively.

Idempotency check: if a `generated`, `uploaded`, or `reconciled` row exists for the same `(applied_from, applied_to)`, abort with a clear message and exit code 10.

### 8.3 Variance Control (upgraded from v1)

Mandatory, not optional. Variance is computed against the most recent successful prior run for the same pair. If no prior run exists (first run), variance checks are skipped and the fact is logged.

| Threshold | Action |
|---|---|
| > 2% week-on-week | Log warning, continue |
| > 5% week-on-week | Hold run, emit alert; requires `--force` flag or approval workflow to proceed |
| > 10% week-on-week | Hard fail regardless of flags; requires human sign-off recorded in ledger |

---

## 9. Oracle Upload — FBDI

### 9.1 Artefact: GlDailyRatesInterface.csv

Standard Oracle FBDI format for daily rates import. One row per currency pair per date range.

### 9.2 Example Output

| From | To | Type | From Date | To Date | Rate | Action |
|---|---|---|---|---|---|---|
| GBP | USD | Corporate | 20/04/2026 | 26/04/2026 | 1.344930 | Insert |
| GBP | EUR | Corporate | 20/04/2026 | 26/04/2026 | 1.147570 | Insert |
| GBP | CAD | Corporate | 20/04/2026 | 26/04/2026 | 1.861730 | Insert |
| GBP | JPY | Corporate | 20/04/2026 | 26/04/2026 | 214.224000 | Insert |

### 9.3 Upload Flow (Phase 1 — file output only)

1. Generate `GlDailyRatesInterface.csv` in a session-scoped working directory
2. Zip per FBDI spec → `GlDailyRatesInterface.zip`
3. Write both files to the evidence pack
4. Update run ledger: `status=generated`
5. Felix notifies via Telegram with workspace path and rates summary

The zip is ready for unmodified manual upload to Oracle (ADFdi or UCM).

### 9.4 Upload Flow (Phase 2 — automated Oracle integration, deferred)

1. Read `GlDailyRatesInterface.zip` from evidence pack (status must be `generated`)
2. Write to UCM via `ErpIntegrationService` (`uploadFileToUcm`)
3. Submit ESS job **Load Interface File for Import** → capture request ID
4. Poll ESS status until terminal state (or timeout)
5. Submit ESS job **Import Daily Rates** → capture request ID
6. Poll until terminal state
7. Update run ledger: `status=uploaded`; record ESS request IDs

All Oracle credentials held in OpenClaw secrets config; no `.env` files committed to the workspace.

---

## 10. Controls & Audit Evidence

### 10.1 Evidence Pack (per run)

Written **before** Oracle upload, to an immutable store (Azure Blob with immutability policy, or equivalent S3 Object Lock):

| File | Contents | Phase |
|---|---|---|
| `boe_raw.csv` | Exact CSV returned by the IADB endpoint | 1 |
| `boe_request.json` | URL, timestamp, response headers, HTTP status | 1 |
| `rates_derived.json` | Parsed, validated rate set | 1 |
| `fbdi_upload.csv` | The exact CSV ready for (or submitted to) Oracle | 1 |
| `manifest.json` | SHA-256 of every file, run_id, timestamps, BoE source date, applied range, variance report | 1 |
| `ess_receipts.json` | ESS request IDs and final statuses | 2 (deferred) |

**Location:** `workspace/fx_rates/evidence/{run_id}/` — written before Oracle upload.

**Retention:** ≥7 years (align with FCA record-keeping requirements and internal audit policy). For long-term retention, configure Felix to sync the evidence directory to Azure Blob (immutability policy) or S3 Object Lock. The workspace copy is the operational record; the blob copy is the audit archive.

### 10.2 Audit-Defensible Statements

- **Source:** Bank of England, a Tier 1 central bank, via its public Interactive Database
- **Licence:** BoE Database Terms permit the use in question
- **Methodology:** Fixed weekly cadence, documented date logic, no discretionary overrides
- **Independence:** Data source is wholly independent of the firm
- **Reproducibility:** Every run can be replayed from evidence artefacts
- **Dual control:** Variance thresholds require human approval above defined tolerances
- **Segregation:** Service account executing uploads has no rate-generation capability; rate-generation process cannot upload without passing validation gates

### 10.3 Reconciliation Job

Separate OpenClaw cron entry (Tuesday ~09:00 London), independent of the upload job:

1. Query Oracle Fusion GL Daily Rates REST resource for the applied week
2. Compare to run ledger (`ledger.db`)
3. Felix sends Telegram report — clean confirmation or mismatch detail
4. Catches silent FBDI failures that would otherwise only surface at month-end close

Cron config:
```json
{
  "schedule": { "kind": "cron", "expr": "0 9 * * 2", "tz": "Europe/London" },
  "payload": { "kind": "agentTurn", "message": "Run FX rate reconciliation for the current week" }
}
```

---

## 11. Exception Handling

| Scenario | Action |
|---|---|
| BoE endpoint unavailable | Retry with exponential backoff (3 attempts); on final failure, alert and abort |
| BoE returns fewer than 4 series | Abort, alert — do not substitute |
| Expected source date missing from response | Apply bank holiday fallback logic, log, continue |
| Rate variance >5% | Hold run, require explicit approval |
| Rate variance >10% | Fail run, require human intervention |
| Oracle UCM upload fails | Phase 2 only: preserve evidence, retry once, then alert |
| ESS job fails | Phase 2 only: preserve evidence, alert, do not re-submit without approval |
| Reconciliation mismatch | Phase 2 only: Felix sends Telegram alert; do not auto-remediate |

---

## 12. Security

- **BoE endpoint:** public, no credentials required
- **Oracle credentials:** stored in OpenClaw secrets config; scoped to FBDI for GL Daily Rates only; rotate via secrets config update
- **Evidence store:** workspace files are readable by Felix only; for audit archive, sync to blob with immutability policy
- **Run ledger:** SQLite in workspace; backed up with standard workspace backup policy
- **Workspace access:** Felix workspace is not shared with other agents or sessions by default
- **No personal data involved** — GDPR assessment recorded but minimal

---

## 13. Implementation

### 13.1 Language and Runtime

Python 3.12, installed into the Felix agent environment. Packaged as a standard library (`pyproject.toml`) and installed into the workspace virtualenv or system Python. Invoked by Felix directly via `agent_api.py` — no separate process or orchestrator required.

The `cli.py` (Typer app) is retained for local development, dry-run testing, and manual operator use, but is not the primary invocation path in production.

### 13.2 Package Structure

Phase 1 scope noted inline. Phase 2 modules stubbed but not implemented.

```
fx_rates/
  providers/
    __init__.py
    base.py          # RateProvider protocol              [Phase 1]
    boe.py           # Primary: BoE IADB client            [Phase 1]
    ecb.py           # Cross-check provider                [Phase 2]
  oracle/
    fbdi_client.py   # UCM upload + ESS submit/poll        [Phase 2]
    csv_builder.py   # GlDailyRatesInterface.csv generation [Phase 1]
  state/
    ledger.py        # Run ledger (SQLite)                  [Phase 1]
    models.py                                              [Phase 1]
  policy/
    dates.py         # resolve_source_date, resolve_applied_window [Phase 1]
    validation.py    # variance thresholds, completeness checks    [Phase 1]
    holidays.py      # UK bank holiday cache                       [Phase 1]
  evidence/
    writer.py        # Artefact writer + manifest           [Phase 1]
  cli.py             # Typer app: run, dry-run, replay, verify
  agent_api.py       # Primary: typed functions called directly by Felix
tests/
  unit/
  integration/
  fixtures/
    boe_responses/   # Recorded real responses for deterministic replay
```

**Phase 1 deliverable:** everything except `oracle/fbdi_client.py` and `providers/ecb.py`. The `oracle/csv_builder.py` is Phase 1 — it generates the file; the client that uploads it is Phase 2.

### 13.3 Key Design Principles

- **Pure functions at the core, side-effects at the edges** — date logic, validation, and transformation contain no I/O and are exhaustively unit-tested
- **Dry-run is first-class** — every CLI command accepts `--dry-run`, which produces the exact FBDI CSV and a diff report without touching Oracle; Felix can invoke dry-run on demand in response to a chat message
- **Structured logging with run correlation** — ULID run_id on every log line, evidence file, and ledger row
- **Explicit exit codes** — agents can reason about outcomes programmatically:
  - `0` = success
  - `10` = already-run
  - `20` = variance-hold
  - `30` = source-unavailable
  - `40` = oracle-failure
  - `50` = reconciliation-mismatch
- **Provider abstraction** — `RateProvider` protocol lets ECB slot in as cross-check or replacement without touching the Oracle or evidence layers

### 13.4 Testing Strategy

- **Unit tests:** all of `policy/` and `providers/` parsing logic, 100% coverage expected
- **Integration tests:** recorded BoE responses replayed through the full pipeline against an Oracle Fusion test pod
- **Contract test:** weekly job in CI that hits the real BoE endpoint and validates shape only (not values), to catch endpoint changes early
- **Chaos test:** randomly inject malformed responses, missing dates, and oversized variance to verify all error paths

### 13.5 Key Libraries

- `requests` — BoE CSV fetch
- `pandas` — CSV parsing and date alignment
- `python-holidays` — UK bank holiday calendar (England and Wales)
- `cx_Oracle` or Oracle REST API client — Fusion write
- `ulid-py` — run ledger IDs
- `typer` — CLI
- `logging` / structured JSON logs — run ledger

---

## 14. Outstanding Items to Confirm

Narrowed from v1; switching to BoE removes several open questions:

1. **Oracle Fusion pod capability:** confirm FBDI is enabled for GL Daily Rates and the service account has required privileges _(critical path — week one)_
2. **BoE licensing confirmation:** record a formal written confirmation of the BoE Database licence terms in the audit memo, covering internal redistribution to ERP
3. **Variance thresholds:** confirm 2% / 5% / 10% tiers align with Treasury policy, or set alternative values
4. **Approval workflow:** variance-hold Telegram messages — who is the recipient? Confirm Telegram user ID(s) for the approval flow
5. **Evidence archive:** Phase 1 uses workspace storage; confirm whether blob sync is required before Phase 2 go-live
6. **Cross-check provider (ECB):** Phase 2 item — confirm whether dual-sourcing is in scope
7. **OpenClaw secrets config:** Oracle Fusion base URL, UCM endpoint, and service account credentials needed for Phase 2; not required for Phase 1

---

## 15. What's No Longer Needed

Removed from v1 scope because the source change eliminates them:

- API key management for rate provider (BoE is public)
- Vendor contract negotiation (no vendor)
- Rate timezone standardisation debate (BoE publishes London-date, unambiguous)
- Mid vs bid-ask discussion (BoE publishes a single mid-market series)
- Scraping/ToS risk assessment (licensed source)

---

## 16. Out of Scope

- Inverse rate population (not required per business rules)
- Intraday rate updates (weekly only)
- Currencies beyond the four listed pairs
- Non-GBP base currencies
