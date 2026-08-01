#!/usr/bin/env python3
"""Validate the generated SeatGeek accounting dataset and print a trial balance.

Checks:
  * every journal entry balances (sum of debits == sum of credits);
  * the aggregate trial balance nets to zero;
  * every GL line references an account in the chart of accounts;
  * every subledger foreign key (vendor, PO, PO line) resolves;
  * goods-receipt amounts never exceed the PO line amount;
  * vendor bills reference known vendors (and POs, when present).

Exits non-zero on any failure so it can gate CI.

Run:  python scripts/validate_seatgeek_dataset.py
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "datasets" / "seatgeek"
D = Decimal


def load(name: str) -> list[dict]:
    with (DATA_DIR / name).open(newline="") as f:
        return list(csv.DictReader(f))


def dec(value: str) -> Decimal:
    return D(value) if value not in ("", None) else D("0")


def main() -> int:
    errors: list[str] = []

    coa = load("chart_of_accounts.csv")
    accounts = {r["account_number"]: r for r in coa}
    acct_type = {r["account_number"]: r["acct_type"] for r in coa}

    subs = {r["subsidiary_id"] for r in load("subsidiaries.csv")}
    vendors = {r["vendor_id"]: r for r in load("vendors.csv")}

    entries = load("journal_entries.csv")
    lines = load("journal_entry_lines.csv")

    # 1. Every JE balances; header totals match line totals.
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for ln in lines:
        by_doc[ln["document_number"]].append(ln)
    for e in entries:
        doc = e["document_number"]
        dr = sum((dec(ln["debit"]) for ln in by_doc[doc]), D("0"))
        crd = sum((dec(ln["credit"]) for ln in by_doc[doc]), D("0"))
        if dr != crd:
            errors.append(f"{doc}: unbalanced Dr {dr} != Cr {crd}")
        if dr != dec(e["total_debit"]) or crd != dec(e["total_credit"]):
            errors.append(f"{doc}: header totals disagree with lines")
        if e["subsidiary_id"] not in subs:
            errors.append(f"{doc}: unknown subsidiary {e['subsidiary_id']}")

    # 2. Trial balance nets to zero; every account is in the COA.
    tb: dict[str, Decimal] = defaultdict(lambda: D("0"))
    for ln in lines:
        acct = ln["account_number"]
        if acct not in accounts:
            errors.append(f"{ln['document_number']}: line account {acct} not in COA")
            continue
        tb[acct] += dec(ln["debit"]) - dec(ln["credit"])
    net = sum(tb.values(), D("0"))
    if net != D("0"):
        errors.append(f"Trial balance does not net to zero: {net}")

    # 3. Subledger referential integrity + receipt <= PO amount.
    pos = {r["po_number"]: r for r in load("purchase_orders.csv")}
    po_lines = load("purchase_order_lines.csv")
    po_line_amt = {(r["po_number"], r["line_id"]): dec(r["amount"]) for r in po_lines}
    for r in po_lines:
        if r["po_number"] not in pos:
            errors.append(f"PO line references unknown PO {r['po_number']}")
        if dec(r["received_amount"]) > dec(r["amount"]):
            errors.append(f"{r['po_number']}/{r['line_id']}: received > PO amount")
        if dec(r["billed_amount"]) > dec(r["amount"]):
            errors.append(f"{r['po_number']}/{r['line_id']}: billed > PO amount")
    for r in pos:
        if pos[r]["vendor_id"] not in vendors:
            errors.append(f"PO {r}: unknown vendor {pos[r]['vendor_id']}")
        if pos[r]["subsidiary_id"] not in subs:
            errors.append(f"PO {r}: unknown subsidiary {pos[r]['subsidiary_id']}")

    for r in load("goods_receipts.csv"):
        key = (r["po_number"], r["po_line_id"])
        if key not in po_line_amt:
            errors.append(f"Receipt {r['receipt_id']}: unknown PO line {key}")
        elif dec(r["amount"]) > po_line_amt[key]:
            errors.append(f"Receipt {r['receipt_id']}: amount exceeds PO line")
        if r["vendor_id"] not in vendors:
            errors.append(f"Receipt {r['receipt_id']}: unknown vendor")

    for r in load("vendor_bills.csv"):
        if r["vendor_id"] not in vendors:
            errors.append(f"Bill {r['bill_id']}: unknown vendor {r['vendor_id']}")
        if r["po_number"] and r["po_number"] not in pos:
            errors.append(f"Bill {r['bill_id']}: unknown PO {r['po_number']}")

    for r in load("zip_requisitions.csv"):
        if r["vendor_id"] not in vendors:
            errors.append(f"Zip req {r['requisition_id']}: unknown vendor")

    for r in load("ad_spend.csv"):
        if r["vendor_id"] not in vendors:
            errors.append(f"Ad spend {r['account_id']}: unknown vendor")

    # ── Report ────────────────────────────────────────────────────────────
    print("SeatGeek dataset — trial balance (FY2026 YTD through 2026-06)\n")
    print(f"  {'Acct':<7}{'Name':<42}{'Type':<14}{'Balance (USD)':>18}")
    print("  " + "-" * 79)
    type_order = ["Bank", "AcctRec", "OthCurrAsset", "FixedAsset", "OthAsset",
                  "AcctPay", "CredCard", "OthCurrLiab", "LongTermLiab", "Equity",
                  "Income", "COGS", "Expense", "OthIncome", "OthExpense"]
    order = {t: i for i, t in enumerate(type_order)}
    total_dr = total_cr = D("0")
    for acct in sorted(tb, key=lambda a: (order.get(acct_type[a], 99), a)):
        bal = tb[acct]
        if bal == 0:
            continue
        if bal > 0:
            total_dr += bal
        else:
            total_cr += -bal
        name = accounts[acct]["account_name"][:40]
        print(f"  {acct:<7}{name:<42}{acct_type[acct]:<14}{bal:>18,.2f}")
    print("  " + "-" * 79)
    print(f"  {'':<63}{'Dr ' + format(total_dr, ',.2f'):>16}")
    print(f"  {'':<63}{'Cr ' + format(total_cr, ',.2f'):>16}")
    print(f"\n  Debits {'==' if total_dr == total_cr else '!='} Credits  "
          f"(difference {total_dr - total_cr:,.2f})")

    print(f"\nChecked {len(entries)} journal entries / {len(lines)} lines, "
          f"{len(accounts)} accounts, {len(vendors)} vendors.")
    if errors:
        print(f"\nFAILED — {len(errors)} issue(s):")
        for e in errors[:50]:
            print(f"  - {e}")
        return 1
    print("\nAll integrity checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
