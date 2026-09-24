"""
Build outputs/submission.csv for hospitals 2-5, and what a reviewer needs next
to it:

  outputs/submission.csv     one row per invoice id, in the template's columns
  outputs/audit_detail.csv   the same rows plus the evidence tier behind each
                             confidence and whether the amount is disputed
  outputs/review_queue.csv   every offending line of every flagged invoice,
                             billed against expected, with the calculation

Every hospital now gets the full audit: contract parsed into the common rule
schema, every line matched and re-priced, structural checks on top. Extraction
for the unlabelled hospitals is checked by src/validate_rules.py, which
measures how many billed lines each parsed rate reproduces exactly.

One row per invoice id, correct invoices included, as the task asks.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from audit import audit, one_row_per_invoice_id, review_queue

OUT_DIR = Path("outputs")
SUBMISSION_HOSPITALS = (2, 3, 4, 5)
SUBMISSION_COLUMNS = [
    "invoice_id",
    "flagged",
    "error_category",
    "expected_total_cents",
    "billed_total_cents",
    "confidence",
]


def build() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (submission, detail, review queue) for the submission hospitals."""
    details, queues = [], []
    for hospital in SUBMISSION_HOSPITALS:
        verdicts, priced, _ = audit(hospital)
        details.append(one_row_per_invoice_id(verdicts))
        queues.append(review_queue(verdicts, priced))
    detail = pd.concat(details, ignore_index=True).sort_values("invoice_id")
    detail["expected_total_cents"] = detail["expected_total_cents"].astype("int64")
    queue = pd.concat(queues, ignore_index=True)
    return detail[SUBMISSION_COLUMNS], detail[SUBMISSION_COLUMNS + ["evidence", "amount_status"]], queue


def write(out_dir: Path = OUT_DIR) -> pd.DataFrame:
    out_dir.mkdir(exist_ok=True)
    submission, detail, queue = build()
    submission.to_csv(out_dir / "submission.csv", index=False)
    detail.to_csv(out_dir / "audit_detail.csv", index=False)
    queue.to_csv(out_dir / "review_queue.csv", index=False)
    return submission


if __name__ == "__main__":
    submission = write()

    print(f"submission.csv: {len(submission)} rows, {int(submission['flagged'].sum())} flagged")
    print()
    print(
        submission.groupby(submission["invoice_id"].str.slice(4, 6))
        .agg(rows=("flagged", "size"), flagged=("flagged", "sum"))
        .to_string()
    )
