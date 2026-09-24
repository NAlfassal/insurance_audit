"""
Evaluate the pipeline against the hospital_1 labels (the dev set).

Scores exactly the verdict that goes into the submission, so every number in
reports/ is reproduced by running this:

  - flagging            precision / recall / F1
  - expected totals     exact to the cent, against the labels' own totals
  - categories          is the *named* error the labelled one, per category
  - calibration         Brier score and accuracy per confidence tier, and
                        how often the amount is exact, per amount status

    python src/evaluate.py            # full pipeline
    python src/evaluate.py --layer1   # structural checks alone
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from audit import audit_hospital, one_row_per_invoice_id

LABELS_PATH = Path("data/labels/hospital_1_labels.csv")
DEV_HOSPITAL = 1


def _banner(title: str) -> None:
    print("=" * 66)
    print(title)
    print("=" * 66)


def _categories(text) -> set[str]:
    return {c for c in str(text if isinstance(text, str) else "").split("|") if c}


class NoDevSet(RuntimeError):
    """The hospital_1 labels do not describe the hospital_1 invoices on disk.

    This report scores predictions against a fixed label file. If the invoices
    are replaced — a re-run on data I have never seen, say — there is nothing
    to score against, and that is not a failure of the audit itself. The
    condition is raised so the caller can skip this report and still produce
    the submission (see src/cli.py).
    """


def main() -> None:
    layer1_only = "--layer1" in sys.argv
    preds = one_row_per_invoice_id(audit_hospital(DEV_HOSPITAL))
    if layer1_only:
        preds["flagged"] = preds["evidence"].eq("structural").astype(int)

    labels = pd.read_csv(LABELS_PATH)
    shared = set(preds["invoice_id"]) & set(labels["invoice_id"])
    if len(shared) < len(labels) / 2:
        raise NoDevSet(
            f"{len(shared)} of {len(labels)} labelled invoice ids are present in "
            f"hospital_{DEV_HOSPITAL}'s invoices; these labels do not describe this data"
        )

    df = preds.merge(labels, on="invoice_id", suffixes=("", "_label"), validate="one_to_one")
    y_pred, y_true = df["flagged"].eq(1), df["is_erroneous"].eq(1)
    tp, fp = int((y_pred & y_true).sum()), int((y_pred & ~y_true).sum())
    fn, tn = int((~y_pred & y_true).sum()), int((~y_pred & ~y_true).sum())
    precision = tp / (tp + fp) if tp + fp else float("nan")
    recall = tp / (tp + fn) if tp + fn else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else float("nan")

    _banner(f"{'STRUCTURAL CHECKS ONLY' if layer1_only else 'FULL PIPELINE'} — hospital_1 (dev set)")
    print(f"  TP={tp}  FP={fp}  FN={fn}  TN={tn}")
    print(f"  precision : {precision:.3f}")
    print(f"  recall    : {recall:.3f}")
    print(f"  F1        : {f1:.3f}")
    if layer1_only:
        return

    exact = df["expected_total_cents"].eq(df["expected_total_cents_label"])
    print()
    _banner("EXPECTED TOTALS — exact to the cent")
    print(f"  {int(exact.sum())} / {len(df)}")
    print(df.loc[~exact, ["invoice_id", "error_categories", "expected_total_cents_label",
                          "expected_total_cents"]].to_string(index=False))

    truth = df["error_categories"].map(_categories)
    named = df["error_category"].map(_categories)
    rows = []
    for category in sorted(set().union(*truth)):
        labelled = truth.map(lambda s: category in s)
        rows.append({
            "category": category,
            "labelled": int(labelled.sum()),
            "named": int((labelled & named.map(lambda s: category in s)).sum()),
        })
    per_category = pd.DataFrame(rows).set_index("category")
    per_category["recall"] = (per_category["named"] / per_category["labelled"]).round(2)
    print()
    _banner("CATEGORIES — labelled error named correctly")
    print(per_category.to_string())
    same_set = (truth == named)[y_true]
    print(f"\n  exact category set on flagged invoices: {int(same_set.sum())} / {int(y_true.sum())}")

    df["correct"] = y_pred.eq(y_true).astype(int)
    print()
    _banner("CALIBRATION")
    print(f"  Brier: {((df['confidence'] - df['correct']) ** 2).mean():.4f}")
    print(df.groupby(["evidence", "confidence"]).agg(n=("correct", "size"), accuracy=("correct", "mean"))
          .round(3).to_string())
    print("\n  The flag and the amount, scored separately:")
    df["amount_exact"] = exact.astype(int)
    print(df.groupby("amount_status").agg(n=("amount_exact", "size"), amount_exact=("amount_exact", "mean"))
          .round(3).to_string())
    # The task asks for confidence "in the row", and a row asserts a total too.
    row_right = df["correct"] * df["amount_exact"]
    print(f"\n  Brier, flag only      : {((df['confidence'] - df['correct']) ** 2).mean():.4f}")
    print(f"  Brier, flag and total : {((df['confidence'] - row_right) ** 2).mean():.4f}")


if __name__ == "__main__":
    main()
