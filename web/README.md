# Accrual Agent — Web Demo UI

An interactive, static demo of the autonomous month-end accrual agent. It
replays the scripted 2026-06 close (days 1 → 10 → final) from checked-in
JSON snapshots — no backend, no credentials, deployable anywhere that serves
a Next.js app.

## Launch chooser: Demo vs MVP

On load, the app presents a **deliberate choice of dataset** before anything
else:

- **Demo walkthrough** (`?mode=demo`) — the seeded toy fixtures (YourCo).
- **MVP — SeatGeek dataset** (`?mode=mvp`) — the same close run against the
  standalone SeatGeek dataset (`datasets/seatgeek/`): NetSuite-shaped chart of
  accounts, real vendor archetypes, Zip commitments, and ad-platform actuals.

Both load from checked-in snapshots (`src/data/{demo,mvp}-data.json`) selected
by the `mode` query param; switch anytime from the header. Everything below is
identical between the two — only the books underneath differ.

## What's in the demo

- **Day stepper** — walk the close day by day (1, 3, 5, 7, 10, Final) with
  per-day narration; arrow keys work, and the current day is deep-linkable
  via `?day=day-7`.
- **Overview** — KPI tiles, register-by-status bar, accrued-vs-posted trend.
- **Register** — sortable/filterable accrual register; click a row for the
  full story: estimate basis, FX, vendor email thread, audit trail, JEs.
- **Journal entries** — the auto-reversing JEs with Dr/Cr accounts and
  reversal dates.
- **Needs attention** — escalations with suggested actions, agent activity
  per run, and the immutable audit trail.
- **Trust ladder** — per-vendor estimate-vs-invoice accuracy streaks; a full
  streak earns estimate auto-posting (Acme completes its third accurate
  period on the final day).
- **Interactive controller review** — on Day 10, approve or reject the two
  held-for-review variances and the sundry sub-floor aggregate, and watch the
  journal entries post. Approvals reveal the line's real final-cycle state
  (nothing is invented client-side).

## Local development

```bash
npm install
npm run dev        # http://localhost:3000
npm run build      # production build (what Vercel runs)
```

## Regenerating the demo data

`src/data/demo-data.json` and `src/data/mvp-data.json` are generated from the
Python agent's scripted demo and checked in so web builds need no Python. To
refresh them after changing the mock scenarios (from the repository root):

```bash
uv run accrual-agent export-web --profile demo    # -> web/src/data/demo-data.json
uv run accrual-agent export-web --profile mvp     # -> web/src/data/mvp-data.json
make export-web                                    # both at once
```

Note: audit timestamps in the export use real wall-clock time (the agent's
simulated clock covers business dates only), so regenerating changes those
values — this is cosmetic.

## Deploying to Vercel

1. Import the repository in Vercel.
2. Set **Root Directory** to `web` (framework auto-detects as Next.js;
   default build/install commands are fine).
3. Deploy — the output is fully static.

Or from the CLI: `vercel --cwd web`.
