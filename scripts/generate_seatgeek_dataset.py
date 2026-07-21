#!/usr/bin/env python3
"""Generate a robust, internally consistent SeatGeek accounting dataset.

The dataset models a NetSuite OneWorld instance for a live-event ticketing
marketplace (SeatGeek-shaped): a full NetSuite-style chart of accounts, the
segment dimensions (subsidiaries, departments, classes, locations), a vendor
master, six months of balanced general-ledger activity for FY2026, and the
detailed procurement subledger (purchase orders, item receipts, vendor bills)
that drives the month-end accrual scenarios for the 2026-06 close.

Everything is deterministic — running this script twice produces byte-identical
output. ``validate_seatgeek_dataset.py`` proves the ties (every journal entry
balances, the trial balance nets to zero, receipts never exceed their PO, and
every foreign key resolves).

Run:  python scripts/generate_seatgeek_dataset.py
Out:  datasets/seatgeek/*.csv  and  datasets/seatgeek/seatgeek_dataset.json
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parents[1] / "datasets" / "seatgeek"

D = Decimal
CENTS = D("0.01")


def money(value: Decimal | int | str) -> Decimal:
    return D(value).quantize(CENTS, rounding=ROUND_HALF_UP)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Chart of accounts  (NetSuite account types, numbers, subtypes)
# ─────────────────────────────────────────────────────────────────────────────
# Columns mirror a NetSuite "Accounts" saved-search export.
# acct_type values are NetSuite's internal type keys.
CHART_OF_ACCOUNTS: list[dict[str, str]] = [
    # ── Assets ────────────────────────────────────────────────────────────
    ("10100", "Operating Bank - JPMorgan Chase", "Bank", "Checking",
     "Primary USD operating account"),
    ("10150", "Operating Bank - Barclays GBP", "Bank", "Checking",
     "UK subsidiary operating account (GBP)"),
    ("10200", "Payroll Bank - SVB", "Bank", "Checking",
     "US payroll funding account"),
    ("10300", "Corporate Money Market", "Bank", "Savings",
     "Short-term treasury investments"),
    ("10400", "Merchant Settlement Clearing - Stripe", "OthCurrAsset", "",
     "Card settlements in transit from payment processor"),
    ("10410", "Merchant Settlement Clearing - Braintree", "OthCurrAsset", "",
     "PayPal/Braintree settlements in transit"),
    ("11000", "Accounts Receivable", "AcctRec", "",
     "Trade receivables (Enterprise clients)"),
    ("11100", "Ticket Buyer Receivable", "OthCurrAsset", "",
     "Authorized card charges not yet settled"),
    ("11500", "Allowance for Doubtful Accounts", "AcctRec", "",
     "Contra-AR reserve"),
    ("12000", "Prepaid Expenses", "OthCurrAsset", "",
     "General prepaids"),
    ("12100", "Prepaid Software Subscriptions", "OthCurrAsset", "",
     "Annual SaaS prepaid, amortized monthly"),
    ("12200", "Prepaid Insurance", "OthCurrAsset", "",
     "D&O and general liability prepaid"),
    ("12300", "Prepaid Marketing & Sponsorships", "OthCurrAsset", "",
     "Upfront team/venue sponsorship prepaid"),
    ("12400", "Prepaid Rent", "OthCurrAsset", "",
     "Office rent paid in advance"),
    ("13000", "Deferred Contract Acquisition Costs", "OthCurrAsset", "",
     "Capitalized enterprise sales commissions (ASC 340-40)"),
    ("14000", "Other Current Assets", "OthCurrAsset", "",
     "Miscellaneous current assets"),
    ("15000", "Computer Equipment", "FixedAsset", "",
     "Laptops, servers, network gear (at cost)"),
    ("15100", "Furniture & Fixtures", "FixedAsset", "",
     "Office furniture (at cost)"),
    ("15200", "Leasehold Improvements", "FixedAsset", "",
     "Buildout of leased office space"),
    ("15300", "Capitalized Software Development", "FixedAsset", "",
     "Internal-use software (ASC 350-40)"),
    ("15900", "Accumulated Depreciation", "FixedAsset", "",
     "Contra-asset for fixed-asset depreciation"),
    ("16000", "Operating Lease ROU Asset", "OthAsset", "",
     "Right-of-use asset for office leases (ASC 842)"),
    ("17000", "Goodwill", "OthAsset", "",
     "Goodwill from acquisitions"),
    ("17100", "Intangible Assets - Developed Technology", "OthAsset", "",
     "Acquired technology and trade names"),
    ("17200", "Accumulated Amortization", "OthAsset", "",
     "Contra-asset for intangible amortization"),
    # ── Liabilities ───────────────────────────────────────────────────────
    ("20000", "Accounts Payable", "AcctPay", "",
     "Trade payables"),
    ("21000", "Accrued Liabilities", "OthCurrLiab", "",
     "General month-end accruals (default accrual credit account)"),
    ("21100", "Accrued Marketing & Advertising", "OthCurrLiab", "",
     "Incurred-but-uninvoiced media and sponsorship spend"),
    ("21200", "Accrued Payroll & Bonus", "OthCurrLiab", "",
     "Earned but unpaid wages, bonus, and commission"),
    ("21300", "Accrued Sales Commissions", "OthCurrLiab", "",
     "Enterprise sales commission payable"),
    ("21400", "Accrued Professional Fees", "OthCurrLiab", "",
     "Legal, audit, and consulting accruals"),
    ("21500", "Accrued Payment Processing Fees", "OthCurrLiab", "",
     "Processor fees incurred but not yet invoiced"),
    ("22000", "Sales Tax Payable", "OthCurrLiab", "",
     "US sales/amusement tax collected"),
    ("22100", "VAT Payable", "OthCurrLiab", "",
     "UK/EU value-added tax collected"),
    ("23000", "Deferred Revenue - Tickets", "OthCurrLiab", "",
     "Buyer fees on tickets to future events"),
    ("23100", "Deferred Revenue - Enterprise SaaS", "OthCurrLiab", "",
     "Unearned platform subscription revenue"),
    ("24000", "Customer Deposits", "OthCurrLiab", "",
     "Prepaid buyer wallet balances / credits"),
    ("24100", "Seller Payable - Marketplace", "OthCurrLiab", "",
     "Face value owed to marketplace ticket sellers"),
    ("25000", "Credit Card Payable", "CredCard", "",
     "Corporate card program balance"),
    ("26000", "Operating Lease Liability - Current", "OthCurrLiab", "",
     "Current portion of lease obligations (ASC 842)"),
    ("26100", "Operating Lease Liability - Long Term", "LongTermLiab", "",
     "Long-term lease obligations (ASC 842)"),
    ("27000", "Deferred Tax Liability", "LongTermLiab", "",
     "Net deferred tax liability"),
    ("28000", "Term Loan Payable", "LongTermLiab", "",
     "Long-term debt / venture facility"),
    # ── Equity ────────────────────────────────────────────────────────────
    ("30000", "Common Stock", "Equity", "",
     "Par value of common shares"),
    ("30100", "Preferred Stock", "Equity", "",
     "Convertible preferred (venture financing)"),
    ("31000", "Additional Paid-in Capital", "Equity", "",
     "Capital raised above par"),
    ("32000", "Accumulated Deficit", "Equity", "",
     "Cumulative retained earnings / (deficit)"),
    ("33000", "Accumulated OCI - FX Translation", "Equity", "",
     "Cumulative translation adjustment"),
    # ── Income ────────────────────────────────────────────────────────────
    ("40000", "Marketplace Revenue - Buyer Fees", "Income", "",
     "Service fees charged to ticket buyers"),
    ("40100", "Marketplace Revenue - Seller Fees", "Income", "",
     "Fees charged to ticket sellers"),
    ("40200", "Primary Ticketing Revenue", "Income", "",
     "Per-ticket fees on SeatGeek Enterprise primary sales"),
    ("40300", "Enterprise SaaS Revenue", "Income", "",
     "Platform subscription / licensing revenue"),
    ("40400", "Sponsorship & Advertising Revenue", "Income", "",
     "On-platform advertising and sponsorship"),
    ("40500", "Delivery & Fulfillment Fee Revenue", "Income", "",
     "Ticket delivery / handling fees"),
    ("40900", "Other Revenue", "Income", "",
     "Miscellaneous revenue"),
    ("49000", "Refunds & Cancellations", "Income", "",
     "Contra-revenue for refunded orders"),
    ("49100", "Chargebacks - Revenue Reversals", "Income", "",
     "Contra-revenue for disputed/charged-back orders"),
    # ── Cost of revenue ───────────────────────────────────────────────────
    ("50000", "Payment Processing Fees", "COGS", "",
     "Interchange and processor fees on GMV"),
    ("50100", "Ticket Fulfillment & Delivery", "COGS", "",
     "Mobile delivery, print-at-home, courier"),
    ("50200", "Chargeback & Fraud Losses", "COGS", "",
     "Net fraud and dispute losses"),
    ("50300", "Customer Support - Cost of Revenue", "COGS", "",
     "Front-line buyer/seller support"),
    ("50400", "Hosting & Infrastructure - COGS", "COGS", "",
     "Production cloud infrastructure serving the marketplace"),
    ("50500", "Content & Data Licensing", "COGS", "",
     "Event, venue, and seat-map data licensing"),
    ("50600", "Partner Revenue Share", "COGS", "",
     "Team/venue/league revenue share on primary sales"),
    # ── Operating expense: Sales & Marketing ──────────────────────────────
    ("60000", "Advertising - Paid Search", "Expense", "",
     "Google Ads and search engine marketing"),
    ("60100", "Advertising - Paid Social", "Expense", "",
     "Meta, TikTok, and social advertising"),
    ("60200", "Advertising - Display & Programmatic", "Expense", "",
     "The Trade Desk and programmatic display"),
    ("60300", "Advertising - TV, Audio & Brand", "Expense", "",
     "Linear/streaming TV, podcast, out-of-home"),
    ("60400", "Affiliate & Partner Marketing", "Expense", "",
     "Affiliate networks and referral partners"),
    ("60500", "Team & Venue Sponsorships", "Expense", "",
     "Naming rights and marketing sponsorship fees"),
    ("60600", "Promotions & Buyer Incentives", "Expense", "",
     "Discount codes and promotional credits"),
    ("60700", "Marketing Creative & Production", "Expense", "",
     "Creative agencies and content production"),
    ("60800", "Field & Event Marketing", "Expense", "",
     "On-site activations and event marketing"),
    ("60900", "Marketing Technology", "Expense", "",
     "Martech, attribution, and CRM tooling"),
    # ── Operating expense: Technology ─────────────────────────────────────
    ("61000", "Cloud Hosting & Infrastructure", "Expense", "",
     "Non-production AWS, staging, and internal tooling"),
    ("61100", "Software Subscriptions", "Expense", "",
     "SaaS applications and licenses"),
    ("61200", "Data & Analytics Platforms", "Expense", "",
     "Warehouse, BI, and analytics tooling"),
    ("61300", "Security & Compliance Tools", "Expense", "",
     "Security, PCI, and compliance tooling"),
    ("61400", "Telecommunications", "Expense", "",
     "SMS, voice, and connectivity"),
    ("61500", "Engineering Contractors", "Expense", "",
     "Contract and staff-aug engineering"),
    # ── Operating expense: People ─────────────────────────────────────────
    ("62000", "Salaries & Wages", "Expense", "",
     "Base salary and wages"),
    ("62100", "Bonus & Commission", "Expense", "",
     "Discretionary bonus and sales commission"),
    ("62200", "Payroll Taxes", "Expense", "",
     "Employer payroll taxes"),
    ("62300", "Employee Benefits", "Expense", "",
     "Health, retirement, and other benefits"),
    ("62400", "Stock-Based Compensation", "Expense", "",
     "Non-cash equity compensation"),
    ("62500", "Recruiting & Hiring", "Expense", "",
     "Agencies, job boards, and relocation"),
    ("62600", "Contract & Temporary Labor", "Expense", "",
     "Non-engineering contractors and temps"),
    ("62700", "Training & Development", "Expense", "",
     "L&D and professional development"),
    # ── Operating expense: G&A ────────────────────────────────────────────
    ("63000", "Legal Fees", "Expense", "",
     "Outside counsel"),
    ("63100", "Audit & Accounting Fees", "Expense", "",
     "External audit, tax prep, and outsourced accounting"),
    ("63200", "Consulting & Professional Services", "Expense", "",
     "Management and technical consulting"),
    ("63300", "Tax Services", "Expense", "",
     "Tax advisory and compliance"),
    ("64000", "Rent & Occupancy", "Expense", "",
     "Office lease expense (ASC 842 straight-line)"),
    ("64100", "Utilities", "Expense", "",
     "Electricity, internet, and building services"),
    ("64200", "Office Supplies & Expenses", "Expense", "",
     "Supplies, snacks, and office operations"),
    ("64300", "Repairs & Maintenance", "Expense", "",
     "Facilities repairs and maintenance"),
    ("64400", "Depreciation Expense", "Expense", "",
     "Depreciation of fixed assets"),
    ("64500", "Amortization Expense", "Expense", "",
     "Amortization of intangibles and cap-dev software"),
    ("65000", "Travel & Entertainment", "Expense", "",
     "Employee travel and client entertainment"),
    ("65100", "Insurance", "Expense", "",
     "D&O, cyber, and general liability"),
    ("65200", "Bank & Merchant Fees - G&A", "Expense", "",
     "Corporate banking and non-COGS merchant fees"),
    ("65300", "Dues & Subscriptions", "Expense", "",
     "Memberships and publications"),
    ("65400", "Bad Debt Expense", "Expense", "",
     "Write-offs of uncollectible receivables"),
    ("65500", "Business Taxes & Licenses", "Expense", "",
     "Franchise tax, licenses, and permits"),
    ("65900", "Other Operating Expense", "Expense", "",
     "Miscellaneous operating expense"),
    # ── Other income / expense ────────────────────────────────────────────
    ("80000", "Interest Income", "OthIncome", "",
     "Interest earned on treasury balances"),
    ("80100", "Other Income", "OthIncome", "",
     "Non-operating income"),
    ("81000", "Interest Expense", "OthExpense", "",
     "Interest on debt facilities"),
    ("81100", "Foreign Exchange Gain/Loss", "OthExpense", "",
     "Realized and unrealized FX"),
    ("82000", "Loss on Disposal of Assets", "OthExpense", "",
     "Fixed-asset disposals"),
    ("90000", "Income Tax Expense", "OthExpense", "",
     "Current and deferred income tax"),
]

COA_FIELDS = ["account_number", "account_name", "acct_type", "subtype", "description"]


# ─────────────────────────────────────────────────────────────────────────────
# 2. Segment dimensions
# ─────────────────────────────────────────────────────────────────────────────
SUBSIDIARIES = [
    {"subsidiary_id": "1", "name": "SeatGeek, Inc. (Consolidated)", "parent": "",
     "country": "US", "currency": "USD", "is_elimination": "false"},
    {"subsidiary_id": "2", "name": "SeatGeek US, Inc.", "parent": "1",
     "country": "US", "currency": "USD", "is_elimination": "false"},
    {"subsidiary_id": "3", "name": "SeatGeek Enterprise, LLC", "parent": "1",
     "country": "US", "currency": "USD", "is_elimination": "false"},
    {"subsidiary_id": "4", "name": "SeatGeek UK Ltd", "parent": "1",
     "country": "GB", "currency": "GBP", "is_elimination": "false"},
]

DEPARTMENTS = [
    {"department_id": "DEP-ENG", "name": "Engineering"},
    {"department_id": "DEP-PROD", "name": "Product & Design"},
    {"department_id": "DEP-MKT", "name": "Marketing"},
    {"department_id": "DEP-SALES", "name": "Sales & Partnerships"},
    {"department_id": "DEP-OPS", "name": "Trust, Support & Operations"},
    {"department_id": "DEP-FIN", "name": "Finance & Accounting"},
    {"department_id": "DEP-GA", "name": "General & Administrative"},
    {"department_id": "DEP-IT", "name": "IT & Security"},
]

CLASSES = [
    {"class_id": "CLS-MKT", "name": "Consumer Marketplace"},
    {"class_id": "CLS-ENT", "name": "SeatGeek Enterprise"},
    {"class_id": "CLS-CORP", "name": "Corporate / Shared"},
]

LOCATIONS = [
    {"location_id": "LOC-NYC", "name": "New York HQ", "subsidiary_id": "2"},
    {"location_id": "LOC-DAL", "name": "Dallas Enterprise Office", "subsidiary_id": "3"},
    {"location_id": "LOC-LON", "name": "London Office", "subsidiary_id": "4"},
    {"location_id": "LOC-REMOTE", "name": "Remote / Distributed", "subsidiary_id": "2"},
]


# ─────────────────────────────────────────────────────────────────────────────
# 3. Vendor master
# ─────────────────────────────────────────────────────────────────────────────
# gl_account/department are the default coding used when a source document does
# not carry its own.  A blank gl_account is an intentionally unmapped vendor.
VENDORS = [
    {"vendor_id": "V-AWS", "name": "Amazon Web Services, Inc.",
     "domain": "aws.amazon.com", "subsidiary_id": "2", "currency": "USD",
     "gl_account": "50400", "department": "DEP-ENG", "class_id": "CLS-CORP",
     "category": "Cloud Infrastructure"},
    {"vendor_id": "V-GOOGLE", "name": "Google LLC",
     "domain": "google.com", "subsidiary_id": "2", "currency": "USD",
     "gl_account": "60000", "department": "DEP-MKT", "class_id": "CLS-MKT",
     "category": "Paid Search"},
    {"vendor_id": "V-META", "name": "Meta Platforms, Inc.",
     "domain": "meta.com", "subsidiary_id": "2", "currency": "USD",
     "gl_account": "60100", "department": "DEP-MKT", "class_id": "CLS-MKT",
     "category": "Paid Social"},
    {"vendor_id": "V-TTD", "name": "The Trade Desk, Inc.",
     "domain": "thetradedesk.com", "subsidiary_id": "2", "currency": "USD",
     "gl_account": "60200", "department": "DEP-MKT", "class_id": "CLS-MKT",
     "category": "Programmatic Display"},
    {"vendor_id": "V-IHEART", "name": "iHeartMedia + Entertainment, Inc.",
     "domain": "iheartmedia.com", "subsidiary_id": "2", "currency": "USD",
     "gl_account": "60300", "department": "DEP-MKT", "class_id": "CLS-MKT",
     "category": "Audio / Brand Media"},
    {"vendor_id": "V-IMPACT", "name": "impact.com",
     "domain": "impact.com", "subsidiary_id": "2", "currency": "USD",
     "gl_account": "60400", "department": "DEP-MKT", "class_id": "CLS-MKT",
     "category": "Affiliate Marketing"},
    {"vendor_id": "V-BSE", "name": "Brooklyn Sports & Entertainment, LLC",
     "domain": "brooklynse.com", "subsidiary_id": "2", "currency": "USD",
     "gl_account": "60500", "department": "DEP-SALES", "class_id": "CLS-MKT",
     "category": "Sponsorship"},
    {"vendor_id": "V-STRIPE", "name": "Stripe, Inc.",
     "domain": "stripe.com", "subsidiary_id": "2", "currency": "USD",
     "gl_account": "50000", "department": "DEP-FIN", "class_id": "CLS-CORP",
     "category": "Payment Processing"},
    {"vendor_id": "V-BRAINTREE", "name": "PayPal / Braintree",
     "domain": "braintreepayments.com", "subsidiary_id": "2", "currency": "USD",
     "gl_account": "50000", "department": "DEP-FIN", "class_id": "CLS-CORP",
     "category": "Payment Processing"},
    {"vendor_id": "V-TWILIO", "name": "Twilio Inc.",
     "domain": "twilio.com", "subsidiary_id": "2", "currency": "USD",
     "gl_account": "61400", "department": "DEP-ENG", "class_id": "CLS-CORP",
     "category": "Communications API"},
    {"vendor_id": "V-SNOWFLAKE", "name": "Snowflake Inc.",
     "domain": "snowflake.com", "subsidiary_id": "2", "currency": "USD",
     "gl_account": "61200", "department": "DEP-ENG", "class_id": "CLS-CORP",
     "category": "Data Platform"},
    {"vendor_id": "V-DATADOG", "name": "Datadog, Inc.",
     "domain": "datadoghq.com", "subsidiary_id": "2", "currency": "USD",
     "gl_account": "61300", "department": "DEP-IT", "class_id": "CLS-CORP",
     "category": "Observability"},
    {"vendor_id": "V-SFDC", "name": "Salesforce, Inc.",
     "domain": "salesforce.com", "subsidiary_id": "3", "currency": "USD",
     "gl_account": "61100", "department": "DEP-SALES", "class_id": "CLS-ENT",
     "category": "CRM"},
    {"vendor_id": "V-ZENDESK", "name": "Zendesk, Inc.",
     "domain": "zendesk.com", "subsidiary_id": "2", "currency": "USD",
     "gl_account": "50300", "department": "DEP-OPS", "class_id": "CLS-MKT",
     "category": "Support Platform"},
    {"vendor_id": "V-KPMG", "name": "KPMG LLP",
     "domain": "kpmg.com", "subsidiary_id": "1", "currency": "USD",
     "gl_account": "63100", "department": "DEP-FIN", "class_id": "CLS-CORP",
     "category": "Audit"},
    {"vendor_id": "V-COOLEY", "name": "Cooley LLP",
     "domain": "cooley.com", "subsidiary_id": "1", "currency": "USD",
     "gl_account": "63000", "department": "DEP-GA", "class_id": "CLS-CORP",
     "category": "Legal"},
    {"vendor_id": "V-RXR", "name": "RXR Realty (902 Broadway)",
     "domain": "rxrrealty.com", "subsidiary_id": "2", "currency": "USD",
     "gl_account": "64000", "department": "DEP-GA", "class_id": "CLS-CORP",
     "category": "Office Lease"},
    {"vendor_id": "V-CONTENTSQ", "name": "Contentsquare SAS",
     "domain": "contentsquare.com", "subsidiary_id": "4", "currency": "GBP",
     "gl_account": "60900", "department": "DEP-MKT", "class_id": "CLS-MKT",
     "category": "Experience Analytics"},
    {"vendor_id": "V-STORMFACT", "name": "Stormfactory Creative Ltd",
     "domain": "stormfactory.example", "subsidiary_id": "4", "currency": "GBP",
     "gl_account": "60700", "department": "DEP-MKT", "class_id": "CLS-MKT",
     "category": "Creative Production"},
    {"vendor_id": "V-APEXSTAFF", "name": "Apex Staffing Partners",
     "domain": "apexstaffing.example", "subsidiary_id": "2", "currency": "USD",
     "gl_account": "", "department": "", "class_id": "",
     "category": "Contract Labor (UNMAPPED)"},
]

VENDOR_FIELDS = ["vendor_id", "name", "domain", "subsidiary_id", "currency",
                 "gl_account", "department", "class_id", "category"]


# ─────────────────────────────────────────────────────────────────────────────
# 4. FX rates (period-end spot, to USD base)
# ─────────────────────────────────────────────────────────────────────────────
FX_RATES = [
    {"currency": "USD", "period": "2026-06", "rate_to_usd": "1.000000"},
    {"currency": "GBP", "period": "2026-06", "rate_to_usd": "1.272000"},
    {"currency": "EUR", "period": "2026-06", "rate_to_usd": "1.084000"},
    {"currency": "CAD", "period": "2026-06", "rate_to_usd": "0.731000"},
]
FX_LOOKUP = {r["currency"]: D(r["rate_to_usd"]) for r in FX_RATES}


# ─────────────────────────────────────────────────────────────────────────────
# 5. General ledger — balanced journal entries
# ─────────────────────────────────────────────────────────────────────────────
# A GL line: (account, debit, credit).  je() asserts each entry balances to
# the cent, so the aggregate trial balance is guaranteed to net to zero.

_je_seq = 0
JOURNAL_ENTRIES: list[dict] = []
JOURNAL_LINES: list[dict] = []


def je(tran_date: dt.date, memo: str, lines: list[tuple[str, Decimal, Decimal]],
       *, subsidiary_id: str = "1", currency: str = "USD",
       entry_type: str = "Standard", reversal_date: dt.date | None = None,
       source: str = "manual") -> str:
    """Record a balanced journal entry; return its document number."""
    global _je_seq
    _je_seq += 1
    doc = f"JE-{tran_date.year}{tran_date.month:02d}-{_je_seq:04d}"
    total_dr = sum((money(d) for _, d, _ in lines), D("0"))
    total_cr = sum((money(c) for _, _, c in lines), D("0"))
    if total_dr != total_cr:
        raise AssertionError(f"{doc} unbalanced: Dr {total_dr} != Cr {total_cr} ({memo})")
    JOURNAL_ENTRIES.append({
        "document_number": doc,
        "tran_date": tran_date.isoformat(),
        "period": f"{tran_date.year}-{tran_date.month:02d}",
        "subsidiary_id": subsidiary_id,
        "currency": currency,
        "entry_type": entry_type,
        "reversal_date": reversal_date.isoformat() if reversal_date else "",
        "source": source,
        "memo": memo,
        "total_debit": str(money(total_dr)),
        "total_credit": str(money(total_cr)),
    })
    for i, (acct, debit, credit) in enumerate(lines, start=1):
        JOURNAL_LINES.append({
            "document_number": doc,
            "line": str(i),
            "account_number": acct,
            "debit": str(money(debit)) if debit else "",
            "credit": str(money(credit)) if credit else "",
            "memo": memo,
            "subsidiary_id": subsidiary_id,
        })
    return doc


ZERO = D("0")


def dr(acct: str, amount: Decimal) -> tuple[str, Decimal, Decimal]:
    return (acct, money(amount), ZERO)


def cr(acct: str, amount: Decimal) -> tuple[str, Decimal, Decimal]:
    return (acct, ZERO, money(amount))


# Opening balance sheet at 2025-12-31 (Assets = Liabilities + Equity).
def opening_balances() -> None:
    lines = [
        dr("10100", D("58200000")),   # operating cash
        dr("10300", D("24000000")),   # money market
        dr("10400", D("9600000")),    # settlements in transit
        dr("11000", D("6400000")),    # AR
        dr("11500", D("-450000")),    # allowance (contra, negative debit)
        dr("12100", D("3100000")),    # prepaid software
        dr("12300", D("5200000")),    # prepaid sponsorships
        dr("15000", D("7300000")),    # computer equipment
        dr("15300", D("21500000")),   # capitalized software
        dr("15900", D("-9800000")),   # accumulated depreciation (contra)
        dr("16000", D("18400000")),   # ROU asset
        dr("17000", D("42000000")),   # goodwill
        dr("17100", D("15600000")),   # developed technology
        dr("17200", D("-6200000")),   # accumulated amortization (contra)
        cr("20000", D("11800000")),   # AP
        cr("21000", D("4300000")),    # accrued liabilities
        cr("21200", D("6900000")),    # accrued payroll
        cr("22000", D("2100000")),    # sales tax payable
        cr("23000", D("31500000")),   # deferred revenue - tickets
        cr("23100", D("7400000")),    # deferred revenue - SaaS
        cr("24100", D("14200000")),   # seller payable
        cr("26000", D("3600000")),    # lease liability current
        cr("26100", D("15900000")),   # lease liability long term
        cr("28000", D("40000000")),   # term loan
        cr("30000", D("12000")),      # common stock
        cr("30100", D("18000")),      # preferred stock
        cr("31000", D("205000000")),  # APIC
    ]
    dr_total = sum((amt for _, amt, _ in lines), ZERO)
    cr_total = sum((amt for _, _, amt in lines), ZERO)
    # Plug accumulated deficit so the opening sheet balances.
    plug = dr_total - cr_total
    if plug >= 0:
        lines.append(cr("32000", plug))
    else:
        lines.append(dr("32000", -plug))
    je(dt.date(2025, 12, 31), "Opening balance sheet — FY2026 carry-in",
       lines, subsidiary_id="1", entry_type="Opening Balance", source="opening")


# Monthly P&L template (base month, USD).  Applied Jan–Jun with slight growth.
# Calibrated to a growth-stage marketplace: heavy sales & marketing, near-
# breakeven-to-modest-loss operating result funded by deferred-revenue float.
BASE_PL = {
    # revenue (credit)
    "40000": D("29000000"), "40100": D("2850000"), "40200": D("5000000"),
    "40300": D("1920000"), "40400": D("760000"), "40500": D("640000"),
    "40900": D("140000"),
    # contra-revenue (debit)
    "49000": D("910000"), "49100": D("360000"),
    # cost of revenue (debit)
    "50000": D("6150000"), "50100": D("1260000"), "50200": D("485000"),
    "50300": D("955000"), "50400": D("1420000"), "50500": D("225000"),
    "50600": D("3120000"),
    # marketing (debit)
    "60000": D("2800000"), "60100": D("2200000"), "60200": D("905000"),
    "60300": D("1510000"), "60400": D("715000"), "60500": D("2420000"),
    "60600": D("1120000"), "60700": D("455000"), "60800": D("305000"),
    "60900": D("255000"),
    # technology (debit)
    "61000": D("1055000"), "61100": D("785000"), "61200": D("325000"),
    "61300": D("185000"), "61400": D("92000"), "61500": D("545000"),
    # people (debit)
    "62000": D("5600000"), "62100": D("1210000"), "62200": D("625000"),
    "62300": D("945000"), "62400": D("1200000"), "62500": D("215000"),
    "62600": D("385000"), "62700": D("72000"),
    # G&A (debit)
    "63000": D("345000"), "63100": D("182000"), "63200": D("425000"),
    "63300": D("92000"), "64000": D("525000"), "64100": D("61000"),
    "64200": D("36000"), "64300": D("41000"), "64400": D("285000"),
    "64500": D("362000"), "65000": D("262000"), "65100": D("152000"),
    "65200": D("71000"), "65300": D("46000"), "65400": D("92000"),
    "65500": D("56000"),
    # other income / expense
    "80000": D("182000"), "81000": D("221000"), "90000": D("140000"),
}

REVENUE_ACCTS = {"40000", "40100", "40200", "40300", "40400", "40500", "40900"}
CONTRA_REV_ACCTS = {"49000", "49100"}

GROWTH = {1: D("0.94"), 2: D("0.97"), 3: D("1.00"),
          4: D("1.03"), 5: D("1.06"), 6: D("1.10")}


def _month_end(month: int) -> dt.date:
    return (dt.date(2026, month, 28) + dt.timedelta(days=4)).replace(day=1) \
        - dt.timedelta(days=1)


def monthly_actuals(month: int) -> None:
    """Emit economically coherent, balanced GL activity for one month.

    Operating activity settles through the merchant-clearing account, accounts
    payable, and accrual accounts.  Month-end treasury entries then sweep the
    clearing balance into operating cash and disburse ~90% of the period's
    payables and accruals, so working-capital balances roll forward and grow at
    a realistic pace rather than ballooning.
    """
    factor = GROWTH[month]
    day = _month_end(month)
    period = f"2026-{month:02d}"
    pl = {acct: money(amt * factor) for acct, amt in BASE_PL.items()}

    liab_added: dict[str, Decimal] = defaultdict(lambda: ZERO)

    def add_liab(acct: str, amount: Decimal) -> tuple[str, Decimal, Decimal]:
        liab_added[acct] += money(amount)
        return cr(acct, amount)

    # Revenue — buyer/seller charges settle into the merchant-clearing account.
    gross_rev = sum((pl[a] for a in REVENUE_ACCTS), ZERO)
    je(day, f"Revenue recognition — {period}",
       [dr("10400", gross_rev)] + [cr(a, pl[a]) for a in sorted(REVENUE_ACCTS)],
       source="revenue")

    # Advance ticket sales for future events increase deferred revenue.
    advance = money(gross_rev * D("0.12"))
    je(day, f"Advance ticket sales deferred — {period}",
       [dr("10400", advance), cr("23000", advance)], source="revenue")

    # Prior deferred revenue earned as those events occur (< advance, so the
    # deferred balance still grows period over period).
    earned = money(advance * D("0.85"))
    je(day, f"Deferred ticket revenue earned — {period}",
       [dr("23000", earned), cr("40000", earned)], source="revenue")

    # Refunds & chargebacks (contra-revenue) refunded out of the clearing account.
    contra_total = sum((pl[a] for a in CONTRA_REV_ACCTS), ZERO)
    je(day, f"Refunds & chargebacks — {period}",
       [dr(a, pl[a]) for a in sorted(CONTRA_REV_ACCTS)] + [cr("10400", contra_total)],
       source="revenue")

    # Cost of revenue — to AP, with a slice accrued (uninvoiced processing fees).
    cogs = [a for a in pl if a.startswith("50")]
    cogs_total = sum((pl[a] for a in cogs), ZERO)
    accr = money(cogs_total * D("0.05"))
    je(day, f"Cost of revenue — {period}",
       [dr(a, pl[a]) for a in sorted(cogs)]
       + [add_liab("21500", accr), add_liab("20000", cogs_total - accr)],
       source="cogs")

    # Sales & marketing — to AP with an accrued-marketing slice.
    mkt = [a for a in pl if a.startswith("60")]
    mkt_total = sum((pl[a] for a in mkt), ZERO)
    accr = money(mkt_total * D("0.15"))
    je(day, f"Sales & marketing expense — {period}",
       [dr(a, pl[a]) for a in sorted(mkt)]
       + [add_liab("21100", accr), add_liab("20000", mkt_total - accr)],
       source="opex")

    # Technology — to AP.
    tech = [a for a in pl if a.startswith("61")]
    tech_total = sum((pl[a] for a in tech), ZERO)
    je(day, f"Technology expense — {period}",
       [dr(a, pl[a]) for a in sorted(tech)] + [add_liab("20000", tech_total)],
       source="opex")

    # Payroll & benefits accrued; bonus accrued; stock comp to APIC (non-cash).
    wages = pl["62000"] + pl["62200"] + pl["62300"]
    je(day, f"Payroll & benefits — {period}",
       [dr("62000", pl["62000"]), dr("62200", pl["62200"]),
        dr("62300", pl["62300"]), add_liab("21200", wages)], source="payroll")
    je(day, f"Bonus & commission accrual — {period}",
       [dr("62100", pl["62100"]), add_liab("21200", pl["62100"])], source="payroll")
    je(day, f"Stock-based compensation — {period}",
       [dr("62400", pl["62400"]), cr("31000", pl["62400"])], source="payroll")
    hiring = pl["62500"] + pl["62600"] + pl["62700"]
    je(day, f"Recruiting, temp labor & training — {period}",
       [dr("62500", pl["62500"]), dr("62600", pl["62600"]),
        dr("62700", pl["62700"]), add_liab("20000", hiring)], source="payroll")

    # G&A — to AP with an accrued professional-fees slice (excludes non-cash D&A).
    ga = [a for a in pl if a.startswith("63") or a.startswith("65")]
    ga += ["64000", "64100", "64200", "64300"]
    ga_total = sum((pl[a] for a in ga), ZERO)
    accr = money(ga_total * D("0.12"))
    je(day, f"General & administrative expense — {period}",
       [dr(a, pl[a]) for a in sorted(ga)]
       + [add_liab("21400", accr), add_liab("20000", ga_total - accr)],
       source="opex")

    # Depreciation & amortization (non-cash).
    je(day, f"Depreciation & amortization — {period}",
       [dr("64400", pl["64400"]), dr("64500", pl["64500"]),
        cr("15900", pl["64400"]), cr("17200", pl["64500"])], source="close")

    # Interest income (treasury), interest expense (accrued to AP), tax (deferred).
    je(day, f"Interest income — {period}",
       [dr("10300", pl["80000"]), cr("80000", pl["80000"])], source="treasury")
    je(day, f"Interest expense — {period}",
       [dr("81000", pl["81000"]), add_liab("20000", pl["81000"])], source="treasury")
    je(day, f"Income tax provision — {period}",
       [dr("90000", pl["90000"]), cr("27000", pl["90000"])], source="tax")

    # ── Month-end cash settlement ──────────────────────────────────────────
    # Sweep the merchant-clearing balance into operating cash.
    clearing_net = gross_rev + advance - contra_total
    je(day, f"Merchant settlement sweep to operating cash — {period}",
       [dr("10100", clearing_net), cr("10400", clearing_net)], source="treasury")

    # Disburse ~90% of the payables/accruals raised this period; the remainder
    # rolls forward, so working-capital liabilities grow gradually.
    pay_ratio = D("0.90")
    settle_lines: list[tuple[str, Decimal, Decimal]] = []
    settle_total = ZERO
    for acct in sorted(liab_added):
        pay = money(liab_added[acct] * pay_ratio)
        if pay > 0:
            settle_lines.append(dr(acct, pay))
            settle_total += pay
    settle_lines.append(cr("10100", settle_total))
    je(day, f"Vendor & payroll disbursements — {period}", settle_lines,
       source="treasury")


# ─────────────────────────────────────────────────────────────────────────────
# 6. Procurement subledger + June accrual scenarios
# ─────────────────────────────────────────────────────────────────────────────
# These records feed the accrual agent's identification engine for the 2026-06
# close.  Each scenario is annotated so the accrual walkthrough is legible.
PURCHASE_ORDERS: list[dict] = []
PO_LINES: list[dict] = []
GOODS_RECEIPTS: list[dict] = []
VENDOR_BILLS: list[dict] = []
ZIP_REQUISITIONS: list[dict] = []
AD_SPEND: list[dict] = []


def po(po_number, vendor_id, subsidiary_id, currency, lines, status="open"):
    PURCHASE_ORDERS.append({
        "po_number": po_number, "vendor_id": vendor_id,
        "subsidiary_id": subsidiary_id, "currency": currency, "status": status,
    })
    for ln in lines:
        PO_LINES.append({"po_number": po_number, **ln})


def receipt(receipt_id, po_number, po_line_id, vendor_id, received_date, amount,
            currency="USD"):
    GOODS_RECEIPTS.append({
        "receipt_id": receipt_id, "po_number": po_number, "po_line_id": po_line_id,
        "vendor_id": vendor_id, "received_date": received_date.isoformat(),
        "amount": str(money(amount)), "currency": currency,
    })


def bill(bill_id, vendor_id, invoice_number, amount, bill_date, po_number="",
         currency="USD", service_period="", scenario=""):
    VENDOR_BILLS.append({
        "bill_id": bill_id, "vendor_id": vendor_id, "invoice_number": invoice_number,
        "po_number": po_number, "amount": str(money(amount)), "currency": currency,
        "bill_date": bill_date.isoformat(), "service_period": service_period,
        "scenario": scenario,
    })


def build_subledger() -> None:
    d = dt.date
    # PO-2101  AWS production hosting — received-not-billed; vendor confirms.
    po("PO-2101", "V-AWS", "2", "USD", [
        {"line_id": "1", "description": "Production cloud infrastructure — Jun 2026",
         "gl_account": "50400", "department": "DEP-ENG", "class_id": "CLS-CORP",
         "amount": "612000.00", "billed_amount": "0.00",
         "received_amount": "487500.00", "service_start": "", "service_end": ""},
    ])
    receipt("IR-3101", "PO-2101", "1", "V-AWS", d(2026, 6, 30), D("487500.00"))
    bill("B-9101", "V-AWS", "AWS-JUN-2026", D("487500.00"), d(2026, 7, 6),
         po_number="PO-2101", service_period="2026-06",
         scenario="July invoice clears the AWS accrual")

    # PO-2102  Stripe processing true-up — received-not-billed; no reply → ladder.
    po("PO-2102", "V-STRIPE", "2", "USD", [
        {"line_id": "1", "description": "Payment processing overage — Jun 2026",
         "gl_account": "50000", "department": "DEP-FIN", "class_id": "CLS-CORP",
         "amount": "268000.00", "billed_amount": "0.00",
         "received_amount": "268000.00", "service_start": "", "service_end": ""},
    ])
    receipt("IR-3102", "PO-2102", "1", "V-STRIPE", d(2026, 6, 29), D("268000.00"))

    # PO-2103  UK creative (GBP, sub 4) — messy reply → LLM parse path.
    po("PO-2103", "V-STORMFACT", "4", "GBP", [
        {"line_id": "1", "description": "UEFA campaign creative — Jun 2026",
         "gl_account": "60700", "department": "DEP-MKT", "class_id": "CLS-MKT",
         "amount": "96000.00", "billed_amount": "0.00",
         "received_amount": "72000.00", "service_start": "", "service_end": ""},
    ])
    receipt("IR-3103", "PO-2103", "1", "V-STORMFACT", d(2026, 6, 30),
            D("72000.00"), currency="GBP")
    bill("B-9103", "V-STORMFACT", "SF-2026-0442", D("72000.00"), d(2026, 7, 11),
         po_number="PO-2103", currency="GBP", service_period="2026-06",
         scenario="GBP invoice clears the creative accrual next period")

    # PO-2104  iHeart brand campaign — service PO, no receipts → prorated PO fallback.
    po("PO-2104", "V-IHEART", "2", "USD", [
        {"line_id": "1", "description": "National audio brand campaign — flight",
         "gl_account": "60300", "department": "DEP-MKT", "class_id": "CLS-MKT",
         "amount": "1800000.00", "billed_amount": "600000.00",
         "received_amount": "0.00", "service_start": "2026-05-01",
         "service_end": "2026-07-31"},
    ])

    # PO-2105  Apex Staffing — received-not-billed but UNMAPPED vendor → escalation.
    po("PO-2105", "V-APEXSTAFF", "2", "USD", [
        {"line_id": "1", "description": "Seasonal support staffing — Jun 2026",
         "gl_account": "", "department": "", "class_id": "",
         "amount": "148000.00", "billed_amount": "0.00",
         "received_amount": "148000.00", "service_start": "", "service_end": ""},
    ])
    receipt("IR-3105", "PO-2105", "1", "V-APEXSTAFF", d(2026, 6, 24), D("148000.00"))

    # PO-2106  RXR office — received-not-billed; no verified contact → blocked send.
    po("PO-2106", "V-RXR", "2", "USD", [
        {"line_id": "1", "description": "Office CAM & operating charges — Jun 2026",
         "gl_account": "64000", "department": "DEP-GA", "class_id": "CLS-CORP",
         "amount": "82500.00", "billed_amount": "0.00",
         "received_amount": "82500.00", "service_start": "", "service_end": ""},
    ])
    receipt("IR-3106", "PO-2106", "1", "V-RXR", d(2026, 6, 15), D("82500.00"))

    # PO-2107  Snowflake — vendor reports higher invoice → variance hold.
    po("PO-2107", "V-SNOWFLAKE", "2", "USD", [
        {"line_id": "1", "description": "Data warehouse consumption — Jun 2026",
         "gl_account": "61200", "department": "DEP-ENG", "class_id": "CLS-CORP",
         "amount": "210000.00", "billed_amount": "0.00",
         "received_amount": "185000.00", "service_start": "", "service_end": ""},
    ])
    receipt("IR-3107", "PO-2107", "1", "V-SNOWFLAKE", d(2026, 6, 30), D("185000.00"))
    bill("B-9107", "V-SNOWFLAKE", "SNOW-INV-77120", D("212400.00"), d(2026, 7, 9),
         po_number="PO-2107", service_period="2026-06",
         scenario="Vendor invoice ($212,400) exceeds accrual ($185,000) — variance")

    # PO-2108  Brooklyn Sports sponsorship — quarterly, prorated service PO.
    po("PO-2108", "V-BSE", "2", "USD", [
        {"line_id": "1", "description": "Barclays Center marketing sponsorship — Q3",
         "gl_account": "60500", "department": "DEP-SALES", "class_id": "CLS-MKT",
         "amount": "900000.00", "billed_amount": "0.00",
         "received_amount": "0.00", "service_start": "2026-06-01",
         "service_end": "2026-08-31"},
    ])

    # Google & Meta: partially billed in NetSuite; remainder covered by API actuals.
    bill("B-9110", "V-GOOGLE", "GOOG-JUN-A", D("2100000.00"), d(2026, 6, 20),
         service_period="2026-06",
         scenario="Google mid-month invoice; API actual exceeds it (net accrual)")
    bill("B-9111", "V-META", "META-JUN-A", D("1650000.00"), d(2026, 6, 22),
         service_period="2026-06",
         scenario="Meta mid-month invoice; API actual exceeds it (net accrual)")

    # Zip approved requisitions (read-only, non-PO committed spend).
    ZIP_REQUISITIONS.extend([
        {"requisition_id": "ZIP-5001", "vendor_id": "V-TTD",
         "vendor_name": "The Trade Desk, Inc.", "business_unit": "BU-US-MKT",
         "committed_amount": "420000.00", "currency": "USD",
         "approved_date": "2026-06-02", "service_start": "2026-06-01",
         "service_end": "2026-06-30", "po_number": "", "gl_account": "60200",
         "department": "DEP-MKT",
         "scenario": "Programmatic committed spend, no PO/bill — Zip gap accrual"},
        {"requisition_id": "ZIP-5002", "vendor_id": "V-IMPACT",
         "vendor_name": "impact.com", "business_unit": "BU-US-MKT",
         "committed_amount": "185000.00", "currency": "USD",
         "approved_date": "2026-06-05", "service_start": "2026-06-01",
         "service_end": "2026-06-30", "po_number": "", "gl_account": "60400",
         "department": "DEP-MKT",
         "scenario": "Affiliate committed spend, no PO/bill — Zip gap accrual"},
        {"requisition_id": "ZIP-5003", "vendor_id": "V-CONTENTSQ",
         "vendor_name": "Contentsquare SAS", "business_unit": "BU-UK-MKT",
         "committed_amount": "48000.00", "currency": "GBP",
         "approved_date": "2026-06-09", "service_start": "2026-06-01",
         "service_end": "2026-06-30", "po_number": "", "gl_account": "60900",
         "department": "DEP-MKT",
         "scenario": "UK analytics committed spend (GBP) — Zip gap accrual"},
    ])

    # Ad-platform actuals (lag-aware; some inside the 72h settle window).
    AD_SPEND.extend([
        {"platform": "google_ads", "account_id": "GAD-118-4420-9917",
         "vendor_id": "V-GOOGLE", "period_start": "2026-06-01",
         "period_end": "2026-06-30", "spend": "3240000.00", "currency": "USD",
         "as_of": "2026-07-01T02:00:00Z",
         "scenario": "Actual vs $2.10M billed → $1.14M net accrual"},
        {"platform": "meta_ads", "account_id": "act_559002841",
         "vendor_id": "V-META", "period_start": "2026-06-01",
         "period_end": "2026-06-30", "spend": "2655000.00", "currency": "USD",
         "as_of": "2026-06-30T22:00:00Z",
         "scenario": "Pulled <72h before close → provisional until settled"},
    ])


# ─────────────────────────────────────────────────────────────────────────────
# 7. Writers
# ─────────────────────────────────────────────────────────────────────────────
def write_csv(name: str, fields: list[str], rows: list[dict]) -> None:
    path = OUT_DIR / name
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Build the general ledger.
    opening_balances()
    for m in range(1, 7):
        monthly_actuals(m)
    build_subledger()

    coa_rows = [dict(zip(COA_FIELDS, row, strict=True)) for row in CHART_OF_ACCOUNTS]

    write_csv("chart_of_accounts.csv", COA_FIELDS, coa_rows)
    write_csv("subsidiaries.csv",
              ["subsidiary_id", "name", "parent", "country", "currency",
               "is_elimination"], SUBSIDIARIES)
    write_csv("departments.csv", ["department_id", "name"], DEPARTMENTS)
    write_csv("classes.csv", ["class_id", "name"], CLASSES)
    write_csv("locations.csv", ["location_id", "name", "subsidiary_id"], LOCATIONS)
    write_csv("vendors.csv", VENDOR_FIELDS, VENDORS)
    write_csv("exchange_rates.csv",
              ["currency", "period", "rate_to_usd"], FX_RATES)
    write_csv("journal_entries.csv",
              ["document_number", "tran_date", "period", "subsidiary_id",
               "currency", "entry_type", "reversal_date", "source", "memo",
               "total_debit", "total_credit"], JOURNAL_ENTRIES)
    write_csv("journal_entry_lines.csv",
              ["document_number", "line", "account_number", "debit", "credit",
               "memo", "subsidiary_id"], JOURNAL_LINES)
    write_csv("purchase_orders.csv",
              ["po_number", "vendor_id", "subsidiary_id", "currency", "status"],
              PURCHASE_ORDERS)
    write_csv("purchase_order_lines.csv",
              ["po_number", "line_id", "description", "gl_account", "department",
               "class_id", "amount", "billed_amount", "received_amount",
               "service_start", "service_end"], PO_LINES)
    write_csv("goods_receipts.csv",
              ["receipt_id", "po_number", "po_line_id", "vendor_id",
               "received_date", "amount", "currency"], GOODS_RECEIPTS)
    write_csv("vendor_bills.csv",
              ["bill_id", "vendor_id", "invoice_number", "po_number", "amount",
               "currency", "bill_date", "service_period", "scenario"], VENDOR_BILLS)
    write_csv("zip_requisitions.csv",
              ["requisition_id", "vendor_id", "vendor_name", "business_unit",
               "committed_amount", "currency", "approved_date", "service_start",
               "service_end", "po_number", "gl_account", "department", "scenario"],
              ZIP_REQUISITIONS)
    write_csv("ad_spend.csv",
              ["platform", "account_id", "vendor_id", "period_start",
               "period_end", "spend", "currency", "as_of", "scenario"], AD_SPEND)

    bundle = {
        "meta": {
            "entity": "SeatGeek, Inc.",
            "erp": "NetSuite OneWorld",
            "base_currency": "USD",
            "fiscal_year": "FY2026",
            "close_period": "2026-06",
            "generated_by": "scripts/generate_seatgeek_dataset.py",
        },
        "chart_of_accounts": coa_rows,
        "subsidiaries": SUBSIDIARIES,
        "departments": DEPARTMENTS,
        "classes": CLASSES,
        "locations": LOCATIONS,
        "vendors": VENDORS,
        "exchange_rates": FX_RATES,
        "journal_entries": JOURNAL_ENTRIES,
        "journal_entry_lines": JOURNAL_LINES,
        "purchase_orders": PURCHASE_ORDERS,
        "purchase_order_lines": PO_LINES,
        "goods_receipts": GOODS_RECEIPTS,
        "vendor_bills": VENDOR_BILLS,
        "zip_requisitions": ZIP_REQUISITIONS,
        "ad_spend": AD_SPEND,
    }
    (OUT_DIR / "seatgeek_dataset.json").write_text(json.dumps(bundle, indent=2) + "\n")

    print(f"Wrote dataset to {OUT_DIR}")
    print(f"  accounts:          {len(coa_rows)}")
    print(f"  vendors:           {len(VENDORS)}")
    print(f"  journal entries:   {len(JOURNAL_ENTRIES)}  "
          f"({len(JOURNAL_LINES)} lines)")
    print(f"  purchase orders:   {len(PURCHASE_ORDERS)}  "
          f"({len(PO_LINES)} lines)")
    print(f"  goods receipts:    {len(GOODS_RECEIPTS)}")
    print(f"  vendor bills:      {len(VENDOR_BILLS)}")
    print(f"  zip requisitions:  {len(ZIP_REQUISITIONS)}")
    print(f"  ad-spend actuals:  {len(AD_SPEND)}")


if __name__ == "__main__":
    main()
