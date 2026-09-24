"""
Audit one hospital: one verdict per invoice, with the evidence behind it.

  1. load       invoices and their own lines, from the JSONL (loader.py)
  2. rules      the contract, reduced to one schema (contract_rules.py)
  3. match      each line to a contracted service, or to none (matcher.py)
  4. price      each line as the contract says (pricing.py)
  5. check      structural breaches that need no contract (layer1_structural.py)
  6. verdict    an invoice is flagged when any line or structural finding fires,
                or when its re-priced total differs from what was billed

Findings are raised per line, not per invoice. The first version only priced an
invoice when every one of its lines matched a service; one unrecognised line
then hid every other error on that invoice, which accounted for 16 of the 25
errors it missed on the dev set.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from contract_rules import load_contract
from layer1_structural import CATEGORIES, structural_checks
from loader import load_hospital
from matcher import UNIT_BASIS, ServiceMatcher
from pricing import price_lines

# Line findings, in the order they are reported.
LINE_FINDINGS = [
    ("unknown", "unknown_service"),
    ("not_yet_contracted", "unknown_service"),
    ("out_of_term", "service_date_out_of_window"),
    ("duplicate", "cross_invoice_duplicate"),
    ("excluded", "exclusion_window_violation"),
    ("capped", "daily_cap_exceeded"),
    ("basis_mismatch", "wrong_unit_basis"),
]

# Confidence by the evidence behind the verdict. On the hospital_1 dev set every
# tier is observed at 1.000 — but the matcher and the pricing conventions were
# developed against that same set, so 1.000 is optimistic. Each tier is held
# below it by how much it leans on things tuned there (see reports/evaluation.md):
CONFIDENCE = {
    # arithmetic and header checks; nothing tuned, nothing contract-specific
    "structural": 0.97,
    # rests on matching, extraction and conventions settled on hospital_1;
    # extraction on hospitals 2-5 is checked only by price agreement
    "line": 0.93,
    # every line identified and re-priced to the cent; what can still hide
    # here is an error type the dev set never contained
    "clean_priced": 0.97,
    # clean, but at least one line could not be identified (none occur in
    # this data; kept so the tier is explicit if one ever does)
    "clean_partial": 0.85,
    # flagged with a daily-cap line: the flag is sure, but the task asks for
    # confidence in the whole ROW, and the row's expected total rests on a cap
    # convention the dev labels contradict on all four of their capped
    # invoices (flag right 4/4, amount right 0/4)
    "amount_disputed": 0.60,
}


def audit_lines(hospital_id: int, data: tuple[pd.DataFrame, pd.DataFrame] | None = None
                ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (invoices, priced lines, structural checks) for one hospital.

    `data` replaces the files on disk with in-memory (invoices, lines), in the
    shape `load_hospital` returns — the error-injection harness uses it.
    """
    invoices, lines = (frame.copy() for frame in data) if data is not None else load_hospital(hospital_id)
    rules = load_contract(hospital_id)
    matcher = ServiceMatcher(rules)

    matches = [
        matcher.match(desc, basis, price, day.date() if pd.notna(day) else None, facility, tier)
        for desc, basis, price, day, facility, tier in zip(
            lines["description"], lines["unit_basis_as_billed"], lines["unit_price_cents"],
            lines["service_date"], lines["facility_code"], lines["plan_tier"],
        )
    ]
    lines["service"] = [m.service for m in matches]
    lines["match"] = [m.how for m in matches]
    lines["contract_basis_billed"] = lines["unit_basis_as_billed"].map(UNIT_BASIS)

    priced = price_lines(lines, rules)
    priced["unknown"] = priced["match"].eq("unknown")
    checks = structural_checks(invoices, lines, rules.contract_number, rules.term[1])
    return invoices, priced, checks


def _line_categories(priced: pd.DataFrame) -> pd.Series:
    """'|'-joined findings per line."""
    parts = [np.where(priced[col], name + "|", "") for col, name in LINE_FINDINGS]
    parts.append(np.where(priced["price_finding"].ne(""), priced["price_finding"] + "|", ""))
    joined = parts[0].astype(object)
    for part in parts[1:]:
        joined = joined + part.astype(object)
    return pd.Series(joined, index=priced.index).str.rstrip("|")


def audit_hospital(hospital_id: int, data=None) -> pd.DataFrame:
    """One row per invoice record, with verdict, category, totals and confidence."""
    return audit(hospital_id, data)[0]


def audit(hospital_id: int, data=None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (verdict per invoice record, priced lines with findings, structural checks)."""
    invoices, priced, checks = audit_lines(hospital_id, data)
    priced["findings"] = _line_categories(priced)

    by_record = priced.groupby("record")
    line_cats = by_record["findings"].agg(lambda s: sorted({c for f in s for c in f.split("|") if c}))
    expected = by_record["expected_line_cents"].sum()
    ambiguous = by_record["match"].agg(lambda s: s.eq("ambiguous").any())

    inv = invoices.set_index("record")
    struct_cats = checks.apply(lambda row: [CATEGORIES[c] for c in checks.columns if row[c]], axis=1)

    rows = []
    for record, invoice in inv.iterrows():
        structural = struct_cats.get(record, [])
        line = line_cats.get(record, [])
        exp = int(expected.get(record, 0))
        total_differs = exp != invoice["invoice_total_cents"]
        # A differing total with no named line finding is still an error: the
        # structural roll-up usually names it, and if not, say so plainly.
        categories = list(dict.fromkeys(structural + line))
        if total_differs and not categories:
            categories = ["invoice_total_mismatch"]
        flagged = bool(categories)

        if "daily_cap_exceeded" in categories:
            tier = "amount_disputed"
        elif structural:
            tier = "structural"
        elif flagged:
            tier = "line"
        elif ambiguous.get(record, False):
            tier = "clean_partial"
        else:
            tier = "clean_priced"

        rows.append(
            {
                "record": record,
                "invoice_id": invoice["invoice_id"],
                "flagged": int(flagged),
                "error_category": "|".join(categories),
                "expected_total_cents": exp,
                "billed_total_cents": int(invoice["invoice_total_cents"]),
                "confidence": CONFIDENCE[tier],
                "evidence": tier,
                # Whether the expected total itself is in doubt, separately
                # from the flag (see the amount_disputed tier above).
                "amount_status": "disputed" if "daily_cap_exceeded" in categories else "exact_per_contract",
            }
        )
    return pd.DataFrame(rows), priced, checks


REVIEW_COLUMNS = [
    "invoice_id", "record", "invoice_categories", "confidence", "amount_status",
    "line_id", "description", "service", "match", "line_findings",
    "quantity", "payable_quantity", "unit_price_cents", "expected_unit_cents",
    "line_total_cents", "expected_line_cents", "calculation",
]


def review_queue(verdicts: pd.DataFrame, priced: pd.DataFrame) -> pd.DataFrame:
    """What a reviewer needs to confirm each flag without re-running anything.

    One row per offending line of each flagged invoice: billed against expected,
    and the calculation that produced the expected figure. An invoice flagged
    only by an invoice-level check (reused id, wrong contract number, a roll-up
    that does not add up) gets a single row with the line fields left blank.
    """
    flagged = verdicts[verdicts["flagged"].eq(1)].rename(columns={"error_category": "invoice_categories"})
    lines = priced.rename(columns={"findings": "line_findings"})
    arithmetic = lines["quantity"] * lines["unit_price_cents"] != lines["line_total_cents"]
    lines.loc[arithmetic, "line_findings"] = (
        lines.loc[arithmetic, "line_findings"].where(lines.loc[arithmetic, "line_findings"].eq(""),
                                                     lines.loc[arithmetic, "line_findings"] + "|")
        + "line_total_arithmetic")
    offending = lines[lines["line_findings"].ne("") | lines["expected_line_cents"].ne(lines["line_total_cents"])]
    queue = flagged.merge(offending.drop(columns=["invoice_id"], errors="ignore"), on="record", how="left")
    # Invoices flagged with no offending line leave the line fields empty; the
    # nullable Int64 type keeps those columns integer cents rather than floats.
    for column in ("line_total_cents", "expected_line_cents", "unit_price_cents", "expected_unit_cents",
                   "quantity", "payable_quantity"):
        queue[column] = queue[column].astype("Int64")
    return queue.sort_values(["invoice_id", "record", "line_id"], na_position="first")[REVIEW_COLUMNS]


def one_row_per_invoice_id(result: pd.DataFrame) -> pd.DataFrame:
    """Collapse records that reuse an invoice id into one submission row.

    The submission is keyed by invoice id, so a reused id can only get one row.
    That row is flagged (the reuse is itself a breach) and carries the later
    record's totals: the first use of an id is the legitimate one, the reuse is
    the error being reported.
    """
    return result.sort_values("record").groupby("invoice_id", as_index=False).last()
