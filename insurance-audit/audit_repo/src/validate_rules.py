"""
Check contract extraction on hospitals that have no labels.

Most invoices are correct (93.6% on the labelled hospital_1). So if a rate,
multiplier or discount had been extracted wrongly, most billed lines for that
service would disagree with it — the error shows up as a service whose billed
prices almost never match. A correctly extracted service agrees on nearly every
line; the few disagreements are the billing errors being looked for.

This is the validation layer for extraction: every service is scored by how
often the billed unit price equals the price the parsed rules produce, and any
service below the threshold is listed for a human to check against the text.

    python src/validate_rules.py
"""

from __future__ import annotations

import pandas as pd

from audit import audit_lines

# A correctly extracted service agrees on almost every line. Below this, the
# extraction is suspect rather than the hospital's billing.
SUSPECT_BELOW = 0.80


def validate(hospital_id: int) -> tuple[dict, pd.DataFrame]:
    _, priced, _ = audit_lines(hospital_id)
    known = priced[priced["service"].notna() & ~priced["not_yet_contracted"]]
    payable = known[known["payable_quantity"] > 0]
    agrees = payable["unit_price_cents"].eq(payable["expected_unit_cents"])

    per_service = (
        payable.assign(agrees=agrees)
        .groupby("service")
        .agg(lines=("agrees", "size"), agreement=("agrees", "mean"))
        .sort_values("agreement")
    )
    summary = {
        "hospital": hospital_id,
        "lines": len(priced),
        "matched": round(priced["service"].notna().mean(), 4),
        "unknown": int(priced["match"].eq("unknown").sum()),
        "ambiguous": int(priced["match"].eq("ambiguous").sum()),
        "price_agreement": round(agrees.mean(), 4),
        "services": len(per_service),
        "suspect_services": int((per_service["agreement"] < SUSPECT_BELOW).sum()),
    }
    return summary, per_service


def main() -> None:
    rows = []
    for hospital_id in (1, 2, 3, 4, 5):
        summary, per_service = validate(hospital_id)
        rows.append(summary)
        suspect = per_service[per_service["agreement"] < SUSPECT_BELOW]
        if len(suspect):
            print(f"\nhospital_{hospital_id}: services to check against the contract text")
            print(suspect.round(3).to_string())
    print()
    print(pd.DataFrame(rows).set_index("hospital").to_string())


if __name__ == "__main__":
    main()
