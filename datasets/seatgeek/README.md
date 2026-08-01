# SeatGeek accounting dataset (NetSuite OneWorld)

A robust, internally consistent accounting dataset for a **SeatGeek-shaped live-event
ticketing marketplace** running on **NetSuite OneWorld**. It provides a
NetSuite-style chart of accounts, the segment dimensions (subsidiaries,
departments, classes, locations), a vendor master, six months of **balanced**
general-ledger activity for FY2026, and the procurement subledger (purchase
orders, item receipts, vendor bills) plus Zip requisitions and ad-platform
actuals that drive the **2026-06 month-end accrual scenarios**.

Everything is **generated deterministically** and **validated**: every journal
entry balances to the cent, the aggregate trial balance nets to zero, and every
foreign key resolves.

```bash
python scripts/generate_seatgeek_dataset.py   # (re)generate all files here
python scripts/validate_seatgeek_dataset.py   # prove the ties + print trial balance
```

The data is emitted both as **CSV** (one file per record type, NetSuite
saved-search-export shaped) and as a single combined **`seatgeek_dataset.json`**.

## Entity structure

| Subsidiary | Currency | Notes |
|---|---|---|
| `1` SeatGeek, Inc. (Consolidated) | USD | Top / consolidation |
| `2` SeatGeek US, Inc. | USD | Consumer marketplace (NYC HQ) |
| `3` SeatGeek Enterprise, LLC | USD | B2B primary ticketing platform (Dallas) |
| `4` SeatGeek UK Ltd | GBP | UK/EU operations (London) |

Segment dimensions mirror NetSuite: **Departments** (`departments.csv`),
**Classes** = business lines (`classes.csv`), **Locations** (`locations.csv`),
and **Subsidiaries** (`subsidiaries.csv`). Period-end spot FX to USD is in
`exchange_rates.csv`.

## Chart of accounts — `chart_of_accounts.csv`

111 accounts using NetSuite's account-type keys (`Bank`, `AcctRec`,
`OthCurrAsset`, `FixedAsset`, `OthAsset`, `AcctPay`, `CredCard`, `OthCurrLiab`,
`LongTermLiab`, `Equity`, `Income`, `COGS`, `Expense`, `OthIncome`,
`OthExpense`). Numbering blocks:

| Range | Section | SeatGeek-specific highlights |
|---|---|---|
| `101xx–172xx` | Assets | Merchant settlement clearing (Stripe/Braintree), ticket-buyer receivable, capitalized software, ROU asset, goodwill |
| `20xxx–28xxx` | Liabilities | **Deferred revenue – tickets** (future-event fees), **seller payable** (marketplace face value), accrued marketing / processing / payroll, lease liabilities |
| `30xxx–33xxx` | Equity | Common/preferred stock, APIC, accumulated deficit |
| `40xxx–49xxx` | Revenue | Buyer & seller marketplace fees, primary ticketing, Enterprise SaaS, sponsorship, delivery; contra-revenue refunds & chargebacks |
| `50xxx` | Cost of revenue | Payment processing, fulfillment, fraud/chargeback losses, hosting, partner revenue share |
| `60xxx` | Sales & marketing | Paid search / social / programmatic / TV-audio, sponsorships, affiliate, promotions |
| `61xxx` | Technology | Cloud, SaaS, data platforms, security, engineering contractors |
| `62xxx` | People | Salaries, bonus/commission, taxes, benefits, stock-based comp |
| `63xxx–65xxx` | G&A | Legal, audit, consulting, occupancy, D&A, insurance |
| `80xxx–90xxx` | Other | Interest income/expense, FX, income tax |

Account **`21000` Accrued Liabilities** is the default credit account for the
accrual agent's journal entries; expense GLs referenced by the accrual
scenarios (e.g. `50400`, `60000`, `60100`, `61200`) are real accounts here.

## General ledger — `journal_entries.csv` + `journal_entry_lines.csv`

- An **opening balance sheet** at 2025-12-31 (Assets = Liabilities + Equity).
- **Six months** (Jan–Jun 2026) of monthly operating activity with slight
  month-over-month growth: revenue recognition, advance-ticket deferral and
  earn-down, refunds/chargebacks, cost of revenue, S&M, technology, payroll &
  stock comp, G&A, depreciation/amortization, interest, and tax.
- Realistic **month-end cash settlement**: merchant clearing is swept to
  operating cash and ~90% of the period's payables/accruals are disbursed, so
  working-capital balances roll forward instead of ballooning.

Each entry is individually balanced, so the **trial balance nets to zero**
(`validate_seatgeek_dataset.py` prints it). The six-month result lands near
break-even with heavy sales & marketing — a credible growth-stage marketplace
profile funded by deferred-revenue float.

## Procurement subledger & accrual scenarios (2026-06 close)

`purchase_orders.csv` / `purchase_order_lines.csv`, `goods_receipts.csv`,
`vendor_bills.csv`, `zip_requisitions.csv`, and `ad_spend.csv` are shaped to
exercise every path of the accrual identification engine. The `scenario` column
on each record explains its role:

| Source | Vendor | Scenario |
|---|---|---|
| `PO-2101` receipt-not-billed | AWS | Confirms by email; July invoice `B-9101` clears it |
| `PO-2102` receipt-not-billed | Stripe | Non-responsive → full reminder ladder + escalation |
| `PO-2103` receipt-not-billed (GBP, sub 4) | Stormfactory | Messy reply → LLM parse path; `B-9103` clears next period |
| `PO-2104` service PO, no receipts | iHeartMedia | Prorated PO-fallback estimate over the flight window |
| `PO-2105` receipt-not-billed, **unmapped** | Apex Staffing | No GL mapping → unmapped-vendor escalation |
| `PO-2106` receipt-not-billed | RXR Realty | No verified contact → blocked send |
| `PO-2107` receipt-not-billed | Snowflake | Vendor invoice `B-9107` ($212,400) exceeds accrual ($185,000) → variance hold |
| `PO-2108` service PO, no receipts | Brooklyn Sports | Quarterly sponsorship prorated to June |
| `ZIP-5001/2/3` approved requisitions | Trade Desk / impact.com / Contentsquare | Committed non-PO spend → Zip gap accrual |
| `ad_spend` actuals | Google / Meta | Platform actuals net of mid-month bills (`B-9110/9111`); Meta pull is inside the 72h settle window → provisional |

## Files

| File | Rows | Description |
|---|---|---|
| `chart_of_accounts.csv` | 111 | NetSuite chart of accounts |
| `subsidiaries.csv` | 4 | OneWorld subsidiaries |
| `departments.csv` / `classes.csv` / `locations.csv` | 8 / 3 / 4 | Segment dimensions |
| `vendors.csv` | 20 | Vendor master with default GL coding |
| `exchange_rates.csv` | 4 | Period-end spot FX to USD |
| `journal_entries.csv` | 109 | GL entry headers (opening + Jan–Jun) |
| `journal_entry_lines.csv` | 562 | GL entry lines |
| `purchase_orders.csv` / `purchase_order_lines.csv` | 8 / 8 | Open POs |
| `goods_receipts.csv` | 6 | Item receipts |
| `vendor_bills.csv` | 5 | AP bills (incl. next-period clears) |
| `zip_requisitions.csv` | 3 | Approved Zip committed spend |
| `ad_spend.csv` | 2 | Google/Meta API actuals |
| `seatgeek_dataset.json` | — | All of the above in one bundle |

> All company, vendor, and account data is **synthetic** and generated for demo
> purposes. It approximates the *shape* of a live-event ticketing marketplace's
> books; it is not SeatGeek's actual financial data.
