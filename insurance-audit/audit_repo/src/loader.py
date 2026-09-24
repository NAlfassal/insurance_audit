"""
Load one hospital's invoices and line items from the nested JSONL.

Why JSONL rather than the two CSVs: line items carry only `invoice_id`, and
some invoice ids are reused. Joining the CSVs on `invoice_id` pools the lines
of two different invoices — often for two different patients — which corrupts
every per-patient rule (daily caps, bundles, exclusion windows). The JSONL
nests each invoice's own lines under it, so each invoice is kept apart by its
position in the file (`record`).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

DATA_DIR = Path("data")

INVOICE_DATE_COLS = ["invoice_date", "admission_date", "discharge_date"]


def load_hospital(hospital_id: int, data_dir: Path = DATA_DIR) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (invoices, lines), both carrying a unique `record` key.

    Dates are coerced, not trusted: an unparseable date becomes NaT, which the
    structural checks report instead of silently dropping.
    """
    path = data_dir / "invoices" / f"hospital_{hospital_id}_invoices.jsonl"
    invoices, lines = [], []
    with path.open(encoding="utf-8") as handle:
        for record, raw in enumerate(handle):
            invoice = json.loads(raw)
            for line in invoice.pop("line_items"):
                lines.append({**line, "record": record})
            invoices.append({**invoice, "record": record})

    invoices = pd.DataFrame(invoices)
    lines = pd.DataFrame(lines)

    for column in INVOICE_DATE_COLS:
        invoices[column] = pd.to_datetime(invoices[column], errors="coerce")
    lines["service_date_raw"] = lines["service_date"]
    lines["service_date"] = pd.to_datetime(lines["service_date"], errors="coerce")

    # Per-patient rules need the patient and invoice date on every line.
    lines = lines.merge(
        invoices[["record", "patient_id", "invoice_date", "facility_code", "plan_tier"]],
        on="record",
        how="left",
    )
    return invoices, lines
