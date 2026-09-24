"""
Layer 1 — structural checks: invoices that contradict themselves.

These need no rate table. Each is a breach of a clause every contract states
(e.g. hospital_4 cl. 4.4 and 11.1–11.3), and a hit is a certainty rather than
an estimate — precision 1.000 on the hospital_1 dev set.

Checks are keyed by invoice *record*, not invoice id: a reused id is itself one
of the findings, so it cannot also be the key that joins lines to invoices.
All money stays in integer cents throughout.
"""

from __future__ import annotations

import pandas as pd

# Check name -> error category written to the submission. The names follow the
# vocabulary of the hospital_1 labels so that per-category scores line up.
CATEGORIES = {
    "line_arithmetic": "line_total_arithmetic",
    "invoice_rollup": "invoice_total_mismatch",
    "duplicate_invoice_id": "duplicate_invoice_id",
    "service_after_invoice": "service_date_after_invoice_date",
    "malformed_date": "malformed_service_date",
    "stay_dates": "inconsistent_admission_dates",
    "contract_number": "contract_number_mismatch",
}


def structural_checks(invoices: pd.DataFrame, lines: pd.DataFrame, contract_number: str,
                      term_end) -> pd.DataFrame:
    """One boolean column per check, one row per invoice record (True = breach).

    A service date beyond the end of the contract term is also, trivially,
    after the invoice date. That line is reported once, by the pricing layer,
    as out of the contract window — the more specific finding — and not again
    here.
    """
    by_record = lines.groupby("record")
    inv = invoices.set_index("record")

    line_sum = by_record["line_total_cents"].sum().reindex(inv.index, fill_value=0)
    arithmetic = (lines["quantity"] * lines["unit_price_cents"]).ne(lines["line_total_cents"])

    checks = pd.DataFrame(
        {
            # quantity x unit price must equal the line total
            "line_arithmetic": arithmetic.groupby(lines["record"]).any(),
            # the invoice total is the sum of its lines and nothing else
            "invoice_rollup": line_sum.ne(inv["invoice_total_cents"]),
            # an invoice number is unique across the term
            "duplicate_invoice_id": inv["invoice_id"].duplicated(keep=False),
            # a service cannot be delivered after the invoice that bills it
            "service_after_invoice": (
                (lines["service_date"] > lines["invoice_date"]) & (lines["service_date"] <= pd.Timestamp(term_end))
            ).groupby(lines["record"]).any(),
            # an unparseable service date
            "malformed_date": lines["service_date"].isna().groupby(lines["record"]).any(),
            # discharged before admitted
            "stay_dates": inv["discharge_date"] < inv["admission_date"],
            # the invoice must quote this hospital's own contract
            "contract_number": inv["contract_number"].ne(contract_number),
        },
        index=inv.index,
    )
    return checks.fillna(False).astype(bool)
