"""
Measure recall on the hospitals that have no labels, by planting errors.

The submission is scored on hospitals 2-5, which have no labels. This harness
gives a recall number for them: audit the hospital, plant ONE known error in
each of up to N invoices it passed (one type per run, each on a different
patient so the plants cannot interact), re-audit, and score each — detected,
correctly named, expected total right — plus collateral: untouched invoices
whose verdict changed.

Limits, stated rather than hidden:
  * "clean" means clean by this pipeline, so an invoice it wrongly passes can
    be picked — that understates recall, never overstates it.
  * Only errors whose true total is known without re-running the pricing under
    test are planted. An omitted premium, bundle or discount is not: knowing
    the right price means re-deriving the rule. Those are measured on
    hospital_1 only.

Run from audit_repo/:  PYTHONPATH=src python src/inject_errors.py
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pandas as pd

from audit import audit
from contract_rules import load_contract
from loader import load_hospital
from matcher import UNIT_BASIS
from pricing import apply_factor

HOSPITALS = (2, 3, 4, 5)
PER_TYPE = 20
OUT = Path("outputs/injection_recall.csv")


def _reprice(inv, lines, row, *, quantity=None, unit_price=None, line_total=None):
    """Change one line and keep the invoice's own arithmetic consistent."""
    old_total = lines.at[row, "line_total_cents"]
    if quantity is not None:
        lines.at[row, "quantity"] = quantity
    if unit_price is not None:
        lines.at[row, "unit_price_cents"] = unit_price
    lines.at[row, "line_total_cents"] = (
        line_total if line_total is not None
        else lines.at[row, "quantity"] * lines.at[row, "unit_price_cents"])
    record = lines.at[row, "record"]
    inv.loc[inv["record"] == record, "invoice_total_cents"] += lines.at[row, "line_total_cents"] - old_total


def _isolated(rules) -> set[str]:
    """Services no cross-line rule touches: no bundle, exclusion, discount,
    premium or cap refers to them. Planting on one of these changes that line
    alone, so any other invoice's verdict moving is the pipeline's doing, not
    a knock-on of the planted change (an altered line also alters the volume
    counts, bundles and exclusion windows of every invoice it interacts with)."""
    linked = set(rules.bundles) | set(rules.exclusions) | set(rules.volume_discounts)
    linked |= set(rules.threshold_premiums) | set(rules.daily_caps)
    linked |= {other for windows in rules.exclusions.values() for _, other in windows}
    return set(rules.base_rates) - linked


def _line(lines, rec, ctx):
    """The first line of `rec` on an isolated service, or None."""
    rows = [r for r in lines.index[lines["record"] == rec] if ctx["service"].get(r) in ctx["isolated"]]
    return rows[0] if rows else None


# Each planter edits one clean invoice in place and returns the total the
# invoice *should* be paid, or None where the true amount is itself a judgement.
# A planter returns False when the invoice offers nothing to plant on.

def plant_unit_price(inv, lines, rec, rules, ctx):
    if (row := _line(lines, rec, ctx)) is None:
        return False
    true_total = ctx["total"]
    # +7% and an odd cent, in integer arithmetic: cannot coincide with an
    # adjustment the contract allows
    _reprice(inv, lines, row, unit_price=int(lines.at[row, "unit_price_cents"]) * 107 // 100 + 1)
    return true_total


def plant_line_arithmetic(inv, lines, rec, rules, ctx):
    if (row := _line(lines, rec, ctx)) is None:
        return False
    _reprice(inv, lines, row, line_total=lines.at[row, "line_total_cents"] + 1000)
    return ctx["total"]


def plant_invoice_total(inv, lines, rec, rules, ctx):
    inv.loc[inv["record"] == rec, "invoice_total_cents"] += 1000
    return ctx["total"]


def plant_unit_basis(inv, lines, rec, rules, ctx):
    if (row := _line(lines, rec, ctx)) is None:
        return False
    billed = lines.at[row, "unit_basis_as_billed"]
    lines.at[row, "unit_basis_as_billed"] = next(s for s in UNIT_BASIS if s != billed and s != "per_hour_per_item")
    return ctx["total"]


def plant_unknown_service(inv, lines, rec, rules, ctx):
    if (row := _line(lines, rec, ctx)) is None:
        return False
    lines.at[row, "description"] = "Hyperbaric Chrono Alignment"
    return ctx["total"]  # an unlisted service is flagged but kept at its billed amount


def plant_daily_cap(inv, lines, rec, rules, ctx):
    rows = [r for r in lines.index[lines["record"] == rec] if ctx["service"].get(r) in rules.daily_caps]
    if not rows:
        return False
    row = rows[0]
    cap = rules.daily_caps[ctx["service"][row]]
    _reprice(inv, lines, row, quantity=cap + 2)
    return None  # the payable amount rests on the cap convention (see decision log)


def _plain(calculation) -> bool:
    """Priced with no bundle, premium, uplift or discount (multipliers allowed)."""
    return isinstance(calculation, str) and calculation.startswith(("base", "dated rate")) and not any(
        word in calculation for word in ("premium", "non-business", "discount", "cap"))


def plant_premium_applied(inv, lines, rec, rules, ctx):
    """A non-business-day uplift charged on a weekday line.

    The uplift is taken from the parsed rules, whose figures the parse-time
    checks tie to the contract text; the true total is the untouched invoice's.
    """
    rows = [r for r in lines.index[lines["record"] == rec]
            if ctx["service"].get(r) in rules.weekend_uplifts
            and pd.notna(lines.at[r, "service_date"]) and lines.at[r, "service_date"].weekday() < 5
            and _plain(ctx["calculation"].get(r))]
    if not rows:
        return False
    row = rows[0]
    uplifted = apply_factor(lines.at[row, "unit_price_cents"], rules.weekend_uplifts[ctx["service"][row]])
    _reprice(inv, lines, row, unit_price=uplifted)
    return ctx["total"]


def plant_discount_applied(inv, lines, rec, rules, ctx):
    """A volume discount given on a line that has not reached the threshold."""
    rows = [r for r in lines.index[lines["record"] == rec]
            if ctx["service"].get(r) in rules.volume_discounts
            and _plain(ctx["calculation"].get(r))]
    if not rows:
        return False
    row = rows[0]
    first_tier = rules.volume_discounts[ctx["service"][row]][0][1]
    _reprice(inv, lines, row, unit_price=apply_factor(lines.at[row, "unit_price_cents"], first_tier))
    return ctx["total"]


def plant_contract_number(inv, lines, rec, rules, ctx):
    inv.loc[inv["record"] == rec, "contract_number"] = "INS-HX-2024-0000"
    return ctx["total"]


def plant_malformed_date(inv, lines, rec, rules, ctx):
    if (row := _line(lines, rec, ctx)) is None:
        return False
    lines.at[row, "service_date_raw"] = "2024-02-30"
    lines.at[row, "service_date"] = pd.NaT
    return None


def plant_out_of_window(inv, lines, rec, rules, ctx):
    if (row := _line(lines, rec, ctx)) is None:
        return False
    lines.at[row, "service_date"] = pd.Timestamp(rules.term[1]) + timedelta(days=42)
    lines.at[row, "service_date_raw"] = lines.at[row, "service_date"].date().isoformat()
    return ctx["total"]  # out-of-term lines are flagged but kept at the billed amount


def plant_after_invoice(inv, lines, rec, rules, ctx):
    if (row := _line(lines, rec, ctx)) is None:
        return False
    moved = lines.at[row, "invoice_date"] + timedelta(days=7)  # same weekday: price unchanged
    if moved > pd.Timestamp(rules.term[1]):
        return False
    lines.at[row, "service_date"] = moved
    lines.at[row, "service_date_raw"] = moved.date().isoformat()
    return None


def plant_cross_duplicate(inv, lines, rec, rules, ctx):
    """A new invoice re-billing one line already billed on `rec`."""
    new_record = int(inv["record"].max()) + 1 + len(ctx["frames"])
    header = inv[inv["record"] == rec].copy()
    if (row := _line(lines, rec, ctx)) is None:
        return False
    line = lines.loc[[row]].copy()
    new_id = f"INV-{header['hospital_id'].iat[0]}-9{new_record:05d}"
    header[["record", "invoice_id", "invoice_total_cents"]] = [new_record, new_id, line["line_total_cents"].iat[0]]
    line[["record", "invoice_id"]] = [new_record, new_id]
    line["line_id"] = line["line_id"] + "-DUP"
    ctx["frames"].append((header, line))
    ctx["score_record"] = new_record
    return 0  # a repeat of a service already billed is not payable


def plant_duplicate_id(inv, lines, rec, rules, ctx):
    """Give `rec` the id of another invoice."""
    other = inv.loc[inv["record"] != rec, "invoice_id"].iat[0]
    inv.loc[inv["record"] == rec, "invoice_id"] = other
    lines.loc[lines["record"] == rec, "invoice_id"] = other
    return None


PLANTERS = {
    "unit_price_mismatch": plant_unit_price,
    "line_total_arithmetic": plant_line_arithmetic,
    "invoice_total_mismatch": plant_invoice_total,
    "wrong_unit_basis": plant_unit_basis,
    "unknown_service": plant_unknown_service,
    "daily_cap_exceeded": plant_daily_cap,
    "premium_incorrectly_applied": plant_premium_applied,
    "volume_discount_incorrectly_applied": plant_discount_applied,
    "contract_number_mismatch": plant_contract_number,
    "malformed_service_date": plant_malformed_date,
    "service_date_out_of_window": plant_out_of_window,
    "service_date_after_invoice_date": plant_after_invoice,
    "cross_invoice_duplicate": plant_cross_duplicate,
    "duplicate_invoice_id": plant_duplicate_id,
}


def run_hospital(hospital_id: int, per_type: int = PER_TYPE) -> pd.DataFrame:
    invoices, lines = load_hospital(hospital_id)
    rules = load_contract(hospital_id)
    baseline, priced, _ = audit(hospital_id, (invoices, lines))
    service = priced.set_index("line_id")["service"]
    calculation = priced.set_index("line_id")["calculation"]
    base_flag = baseline.set_index("record")["flagged"]

    clean = baseline[baseline["flagged"].eq(0)]
    # one planted invoice per patient, so planted errors cannot interact
    clean = clean.merge(invoices[["record", "patient_id"]], on="record").drop_duplicates("patient_id")

    isolated = _isolated(rules)
    results = []
    for category, plant in PLANTERS.items():
        inv, lin = invoices.copy(), lines.copy()
        by_line = dict(zip(lin.index, lin["line_id"].map(service)))
        calc_of = dict(zip(lin.index, lin["line_id"].map(calculation)))
        frames: list = []
        planted: dict[int, int | None] = {}
        for rec, total in zip(clean["record"], clean["expected_total_cents"]):
            if len(planted) == per_type:
                break
            ctx = {"total": int(total), "service": by_line, "frames": frames, "isolated": isolated,
                   "calculation": calc_of}
            truth = plant(inv, lin, rec, rules, ctx)
            if truth is False:
                continue
            planted[ctx.get("score_record", rec)] = truth
        for header, line in frames:
            inv = pd.concat([inv, header], ignore_index=True)
            lin = pd.concat([lin, line], ignore_index=True)

        verdicts = audit(hospital_id, (inv, lin))[0].set_index("record")
        hit = verdicts.loc[list(planted)]
        amounts = [(verdicts.at[r, "expected_total_cents"] == t) for r, t in planted.items() if t is not None]
        untouched = verdicts.index.intersection(base_flag.index).difference(list(planted))
        results.append({
            "hospital": hospital_id,
            "planted": category,
            "n": len(planted),
            "detected": int(hit["flagged"].sum()),
            "named": int(hit["error_category"].str.split("|").apply(lambda c: category in c).sum()),
            "amount_checked": len(amounts),
            "amount_exact": int(sum(amounts)),
            "collateral": int((verdicts.loc[untouched, "flagged"] != base_flag.loc[untouched]).sum()),
        })
    return pd.DataFrame(results)


def main() -> pd.DataFrame:
    table = pd.concat([run_hospital(h) for h in HOSPITALS], ignore_index=True)
    OUT.parent.mkdir(exist_ok=True)
    table.to_csv(OUT, index=False)
    by_type = table.groupby("planted", sort=False)[["n", "detected", "named", "amount_checked",
                                                   "amount_exact", "collateral"]].sum()
    print(by_type.to_string())
    print()
    print(table.groupby("hospital")[["n", "detected", "named", "collateral"]].sum().to_string())
    return table


if __name__ == "__main__":
    main()
