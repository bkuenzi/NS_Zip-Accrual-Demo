# Design decisions & roadmap — accrual agent v2

Outcome of a structured product interview (2026-07-19). Context: the project's
primary purpose right now is a **demo that wins a customer/stakeholder**, with
a **controller / accounting manager in the room**. The areas of most concern
were **accounting correctness** and the **vendor email loop**. The static web
demo stays static; autonomy grows with track record.

## Decisions

### 1. Confirmation routing is configurable: internal budget owner vs vendor
Some categories should never ping the vendor (it invites early invoices and
reveals process); others (agencies, usage-billed platforms) are exactly where
vendor-side confirmation shines. Routing is per-vendor in
`config/gl_mappings.yaml` (`confirmation_routing`), defaulting to `vendor`,
with `internal` routes sending the request to a named internal owner
(`internal_owners`). Internal owner emails must be on the company's own domain
(derived from the accruals mailbox) — the same hard-gate philosophy as the
vendor-master domain cross-check. Confirmations arriving on an internal route
record `confirmed_source: internal_reply`.

### 2. Vendors state the delivery basis; replies are templated
Straight-line proration of service POs is the opening estimate, not the
final word. The confirmation email now embeds a fill-in reply block
(`AMOUNT / CURRENCY / DELIVERED PERCENT / INVOICE NUMBER / EXPECTED INVOICE
DATE`). A deterministic template parser runs before the free-text heuristics;
a stated `DELIVERED PERCENT` on a service-PO line replaces the proration as
the line's recorded estimate basis. Free text + heuristics + LLM fallback all
remain for vendors who ignore the template.

### 3. LLM extractions require second-pass verification
An LLM-extracted amount never auto-confirms on its own. A second,
independent verification pass must agree with the extraction
(`LLMExtractor.verify`); disagreement or verification failure routes the line
to **held-for-review** with the raw email attached, and raises an
`unverified_extraction` escalation. Deterministic (template/heuristic) parses
are unaffected.

### 4. Vendor's number wins inside the variance gate (unchanged, affirmed)
Within the applicable threshold the line confirms at the vendor's number —
that is the point of confirming. Beyond it, held for review with both numbers
shown. (Already implemented; the interview affirmed the posture.)

### 5. Aggregate sub-floor materiality check
Per-line floors are standard, but forty $200 subscriptions are still an $8k
misstatement. Sub-floor gaps are now accumulated per period; when their
base-currency total crosses `ACCRUAL_SUBFLOOR_AGGREGATE_THRESHOLD` (default
$1,000) the agent raises a single **sundry accruals** register line
(comm-suppressed, coded via `sundry` in `gl_mappings.yaml`) that lands in the
review queue. A human approving it posts one bulk estimate-based JE — the
per-line posting gate is never bypassed.

### 6. Trust ladder: measured accuracy streak unlocks estimate auto-posting
Autonomy is earned per vendor: when every cleared accrual for a vendor lands
within `ACCRUAL_TRUST_TOLERANCE_PCT` (default ±3%) of its eventual invoice for
`ACCRUAL_TRUST_STREAK_PERIODS` (default 3) consecutive periods, that vendor's
unconfirmed estimates auto-post on the final close day instead of queueing for
approval. Streaks are computed from actuals (cleared invoice amounts recorded
at reconcile time), displayed on the dashboard, reset by any miss, and
revocable via `trust_ladder.revoked` in `gl_mappings.yaml`. Auto-unlock was
chosen over unlock-plus-ratification: the dashboard shows the streak and the
controller can revoke at any time.

### 7. FX uses NetSuite's own currency-rate table
JEs are posted in transaction currency at the rate NetSuite's `currencyrate`
table holds for the period end — the ledger stays self-consistent and there is
no rate argument to have with a controller. (The live client already queried
`currencyrate`; docs and the mock now say so explicitly.)

### 8. Per-line approval stays
Batch approval of unconfirmed estimates at close was considered and rejected:
each estimate-based post deserves individual eyes, and 40 approvals is still
far faster than hand-building 40 JEs. The trust ladder (decision 6) is the
sanctioned way volume leaves the queue.

### 9. Demo narrative: build around "the catch" and "approve → post"
The controller-facing emotional peaks are (a) the agent catching a materially
different confirmed amount and *refusing to post it*, and (b) the controller
clicking approve and watching a correctly-formed auto-reversing JE land.
Narration and the web walkthrough foreground those two moments.

## Out of scope (deliberately, for now)
- Hosted micro-form reply links (breaks the no-backend posture; templated
  email replies chosen instead).
- Month-average / configurable FX policies (NetSuite's table is the answer
  that survives scrutiny).
- Batch approval UX (see decision 8).
- Live NetSuite pilot hardening — the demo is the current product.

## Sequencing
1. Engine: routing, templated replies + vendor-stated basis, LLM second pass,
   sub-floor aggregate, trust ladder, FX docs (this change set).
2. Demo: mock scenarios + narration reworked around the two climax moments;
   web snapshot regenerated; trust-ladder panel added to the overview.
3. Later: live-pilot hardening once a design partner is signed.
