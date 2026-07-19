# NS_Zip Accrual Agent

An autonomous accounting accrual agent for month-end close. It identifies
incurred-but-uninvoiced spend across **NetSuite** and **Zip**, confirms it via
**vendor email** or connected **vendor APIs** (Google Ads, Meta), maintains a
live accrual register, and writes auto-reversing **journal entries back to
NetSuite** — escalating to humans exactly where judgment is required.

```
             ┌────────────┐   ┌─────────┐   ┌───────────────────┐
   reads ──▶ │  NetSuite  │   │   Zip   │   │ Google Ads / Meta │
             │ POs, GRs,  │   │ approved│   │  spend actuals    │
             │ bills, FX  │   │ reqs    │   │  (lag-aware)      │
             └─────┬──────┘   └────┬────┘   └─────────┬─────────┘
                   ▼               ▼                  ▼
             ┌─────────────────────────────────────────────────┐
             │        identification / API-accrual engine      │
             │   receipts-not-billed · PO proration · Zip gaps │
             └────────────────────────┬────────────────────────┘
                                      ▼
   ┌──────────────┐         ┌──────────────────┐        ┌────────────────┐
   │ vendor email │ ◀─────▶ │ ACCRUAL REGISTER │ ─────▶ │ NetSuite JEs   │
   │ SMTP / IMAP  │ confirm │  (SQLite + audit)│  post  │ auto-reversing │
   │ + reminders  │         └──────────────────┘        └────────────────┘
   └──────────────┘             │           │
                                ▼           ▼
                        review queue   exception report
                        (CLI, human)   + HTML dashboard
```

## Quick start (no credentials needed)

```bash
make install          # uv sync --extra dev
make demo             # scripted 2026-06 close: days 1 → 3 → 5 → 7 → 10 + review
make test             # pytest suite
```

The demo runs entirely against seeded mock adapters and walks every path:
receipt-based and prorated estimates, Zip non-PO spend, ad-platform
auto-confirmation with 72h restatement, escalating reminders, a structured
reply block with a vendor-stated delivery basis, an LLM-fallback-parsed
European reply with second-pass verification, an internal-budget-owner
confirmation route, sub-floor gaps aggregating into a sundry accrual, a
disputed amount held for review, human approval posting JEs, invoice
matching/clearing that feeds per-vendor trust-ladder accuracy streaks, and
team-lead escalations. It ends with `output/dashboard_2026-06.html` and a
checkpoint exception report.

## Web demo UI (Vercel-deployable)

`web/` contains an interactive Next.js demo that replays the scripted close
from a checked-in JSON snapshot — day stepper with narration, KPI tiles and
charts, drill-in accrual register (vendor threads, audit trail), journal
entries, and a live controller-review step where you approve the held
variances in the browser. No backend or credentials required.

```bash
cd web && npm install && npm run dev   # local
uv run accrual-agent export-web        # regenerate web/src/data/demo-data.json
```

To deploy: import the repo in Vercel and set **Root Directory** to `web`
(everything else is auto-detected). Details in [`web/README.md`](web/README.md).

## How it works

### 1. Identification (`engine/identification.py`)
Every cycle, uninvoiced gaps become register lines with a stated estimate basis:

| Source | Estimate basis |
|---|---|
| NetSuite goods receipts | received value − billed value per PO |
| NetSuite service POs (no receipts) | PO amount prorated to the period's share of the service window |
| Zip approved requisitions (non-PO) | committed spend − matching AP bills |
| Google Ads / Meta APIs | platform actuals − posted NetSuite invoices |

Gaps below the materiality floor (default $250 base-currency) are logged, never
accrued or emailed individually — but when they sum past
`ACCRUAL_SUBFLOOR_AGGREGATE_THRESHOLD` (default $1,000) they raise one
comm-suppressed **sundry accruals** line that a human must approve into a
single bulk estimate-based JE. Zip is **read from only** — the adapter has no
write surface.

### 2. Confirmation
- **API-sourced lines** are `auto_confirmed` from data and vendor email is
  suppressed. Inside the platform's settle window (default 72h) they stay
  `provisional`: re-pulled and adjusted each cycle, posted once settled (or
  forced with the latest number on the final close day).
- **Everything else** gets an outbound confirmation request — routed per
  `confirmation_routing` in `gl_mappings.yaml` to either the **verified vendor
  contact** or an **internal budget owner** (whose address must be on the
  company domain) — then escalating reminders on close days 3 / 7 / 10
  (configurable). The email embeds a fill-in reply block (amount, currency,
  delivered %, invoice number/date); a stated delivered % on a service PO
  replaces straight-line proration as the estimate basis.
- Replies are matched by a `[ACR-…]` reference token + `In-Reply-To` headers
  and parsed template-first, then by deterministic heuristics, with a Claude
  fallback (`ACCRUAL_LLM_MODEL`, default `claude-haiku-4-5`) when confidence
  is low. An **LLM extraction never auto-confirms alone** — an independent
  second verification pass must agree, or the line is held for review. PDF
  attachments are saved and text-extracted as corroboration — they never
  solely auto-confirm.
- A confirmed amount within the variance threshold (global ±5%, with
  per-vendor / per-GL overrides) confirms the line at the respondent's number;
  beyond it, the line is **held for review** with both amounts shown.

### 3. Hard gates (enforced in code, tested)
- A journal entry posts **only** from `confirmed`/`auto_confirmed` status —
  `estimated — pending confirmation` can never post. On the final close day an
  unconfirmed line goes to the review queue; a human approving it posts an
  explicitly ESTIMATE-BASED JE.
- **Autonomy is earned, per vendor**: when every cleared accrual lands within
  `ACCRUAL_TRUST_TOLERANCE_PCT` (±3%) of the actual invoice for
  `ACCRUAL_TRUST_STREAK_PERIODS` (3) consecutive periods, that vendor's
  unconfirmed estimates auto-post on the final close day (memo-tagged
  `ESTIMATE-BASED (trust-ladder auto-post)`). Any miss resets the streak;
  controllers revoke via `trust_ladder.revoked` in `gl_mappings.yaml`.
- Outbound email is **blocked** unless a contact is `verified: true` **and**
  its domain cross-checks against the NetSuite vendor master (managed via
  `accrual-agent contacts add|verify`, audit-logged).
- JE `externalId` is a deterministic hash of vendor|period|reference, so a
  duplicate post is rejected by NetSuite itself even if local state is lost.
- Every status/amount change appends to an immutable audit trail with actor
  attribution (`review approve --as controller@co.com`).

### 4. Write-back & reversal (`engine/writeback.py`)
JEs debit the expense GL and credit accrued liabilities (memo: vendor, period,
PO/req, line id), posted in transaction currency at the rate from **NetSuite's
own currency-rate table** (period-end effective — the ledger stays
self-consistent), to the subsidiary inherited from the source document. Each JE carries
`reversalDate` = day 1 of the next period (NetSuite auto-reversal). The
reconcile pass matches arriving invoices (exact PO reference; tolerant
vendor+period+amount for non-PO; ambiguous matches go to a human) and marks
lines `cleared`; posted accruals unmatched beyond the lookback window (default
2 periods) escalate as stale.

### 5. Escalation & reporting
Escalations (non-responsive vendor, API failure/anomaly, variance breach,
unmapped vendor, missing contact, close risk, stale accrual) dispatch via
configurable channels — team-lead **email**, **Slack** webhook, or both — once
per issue, re-raising with elevated urgency after 2 business days or at
checkpoints. Day 5 / Day 10 / final-day checkpoints push the exception report
with the dashboard attached. Every run rewrites a self-contained HTML
dashboard: KPI tiles, needs-attention queue first, collapsible register /
threads / audit sections.

## CLI

```text
accrual-agent run-cycle [--close-day N]   full daily cycle (cron/Actions/Airflow-driven)
accrual-agent status [--full]             what needs attention, first
accrual-agent review list|approve|reject  human review queue; approve posts the JE immediately
accrual-agent contacts add|verify         verified-contact management (unblocks sends)
accrual-agent poll-inbox / send-requests / post-accruals / reverse-check
accrual-agent report / dashboard          regenerate outputs
accrual-agent doctor                      config + connectivity checks
accrual-agent demo                        scripted end-to-end walkthrough
```

Concurrent safety: mutating commands take an advisory lock (SQLite is WAL-mode)
and exit cleanly with "run in progress" rather than interleaving.

## Configuration

Copy `.env.example` to `.env`. Highlights:

| Setting | Purpose |
|---|---|
| `ACCRUAL_MODE` | `mock` (seeded adapters, forced dry-run email) / `live` |
| `ACCRUAL_OUTBOUND_MODE` | `dry_run` (default) / `sandbox` (allowlist/redirect) / `live` |
| `ACCRUAL_REMINDER_DAYS` | reminder cadence, default `[3,7,10]` |
| `ACCRUAL_VARIANCE_THRESHOLD_PCT` | global gate, default ±5% (overrides in `config/gl_mappings.yaml`) |
| `ACCRUAL_MATERIALITY_FLOOR` | default 250.00 base currency |
| `ACCRUAL_SUBFLOOR_AGGREGATE_THRESHOLD` | sub-floor gaps summing past this raise one sundry line (default 1000.00) |
| `ACCRUAL_TRUST_STREAK_PERIODS` / `ACCRUAL_TRUST_TOLERANCE_PCT` | trust-ladder streak length (3) and accuracy tolerance (±3%) |
| `ACCRUAL_AD_SETTLE_HOURS` | ad restatement window, default 72 |
| `ACCRUAL_ESCALATION_CHANNELS` | `email`, `slack`, or `email,slack` |
| `NETSUITE_*` | Token-Based Auth (OAuth 1.0a): account id, consumer + token key/secret |
| `ZIP_API_KEY`, `GOOGLE_ADS_*`, `META_*` | integration credentials |
| `SMTP_* / IMAP_*`, `ACCRUAL_MAILBOX_ADDRESS` | dedicated accruals mailbox |
| `ANTHROPIC_API_KEY`, `ACCRUAL_LLM_MODEL` | optional LLM reply-extraction fallback |

Files under `config/`:
- `gl_mappings.yaml` — vendor → GL/cost center, Zip BU → subsidiary,
  ad account → vendor/subsidiary, variance overrides, confirmation routing
  (vendor vs internal owner) + internal owners, sundry coding, trust-ladder
  revocations
- `vendor_contacts.yaml` — verified contacts (managed by the CLI)
- `close_calendar.yaml` — fiscal calendar (calendar-month default; custom 4-4-5
  periods supported), business-day close-day derivation, final close day
- `templates/*.j2` — accounting-editable email/report templates

## Project layout

```
src/accrual_agent/
  config.py fiscal.py models.py runtime.py locking.py logging_setup.py
  register/       repository.py (SQLite + audit)  service.py (status machine)
  integrations/   base.py (retries/errors)  netsuite/ zip_client/ google_ads/
                  meta_ads/ (real client + mock each)  factory.py
  comms/          mailer.py outbound.py inbound.py llm_extractor.py
                  cadence.py templates.py
  engine/         identification.py api_accruals.py confirmation.py
                  writeback.py escalation.py close_cycle.py trust.py
  reporting/      exception_report.py dashboard.py
  cli.py
tests/            invariants, parsers, cadence, gates, respx-mocked HTTP
                  clients (OAuth signing, pagination, retries), full e2e
```

## Production deployment

Run `accrual-agent run-cycle` daily from any scheduler (cron, GitHub Actions,
Airflow) with `ACCRUAL_MODE=live`. The cycle is idempotent — reminders are
keyed by (thread, stage), JEs by deterministic external id — so re-runs after
partial failures are always safe. Stage failures are isolated: a dead ad API
marks its lines and escalates without stalling vendor comms or posting.
