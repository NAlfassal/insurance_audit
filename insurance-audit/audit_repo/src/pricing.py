"""
Re-price every line the way its contract says, and record why it differs.

Order and rounding are fixed by the contracts (e.g. hospital_1 §3, hospital_4
§4, hospital_5 §3.3), and both matter:

  rate:  base rate for the service date (or bundled rate)
         -> facility factor -> plan-tier factor
         -> threshold premium -> non-business-day uplift
         -> cumulative volume discount
         rounding to the cent, halves away from zero, after EACH step
  qty:   capped at the daily maximum for the patient, across all invoices

Premiums, discounts, caps, bundles, exclusion windows and duplicates all look
across lines and invoices, so the whole hospital is walked once in service-date
then line-id order — the order the contracts prescribe for cumulative counts.

Conventions the text leaves open were settled against the hospital_1 labels,
not chosen (see reports/decision_log.md).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from itertools import product

import pandas as pd

from contract_rules import ContractRules


def round_half_up(value: Fraction) -> int:
    """Nearest cent, exact halves away from zero (Python's round() is banker's)."""
    whole = math.floor(abs(value) + Fraction(1, 2))
    return whole if value >= 0 else -whole


def apply_factor(cents: int, factor: Fraction) -> int:
    """cents x factor, rounded half up — computed exactly, in integers.

    In binary floating point 20625 * 0.7 is 14437.4999…, so a float product
    rounds a true half (14437.5) the wrong way. Contract factors are exact
    fractions read from the contract's own digits (0.7 -> 7/10, +20% -> 6/5),
    so the product is exact. A float is refused rather than converted: money
    never passes through floating point anywhere in the pipeline.
    """
    if not isinstance(factor, Fraction):
        raise TypeError(f"factor must be an exact Fraction, not {type(factor).__name__}")
    return round_half_up(int(cents) * factor)


def money(cents: int) -> str:
    """130125 -> '1301.25', by integer division (for the calculation trail)."""
    sign = "-" if cents < 0 else ""
    pounds, pence = divmod(abs(int(cents)), 100)
    return f"{sign}{pounds}.{pence:02d}"


@dataclass(frozen=True)
class Adjustments:
    """Which optional adjustments apply to one line."""

    bundle: bool = False
    premium: bool = False
    weekend: bool = False
    discount: Fraction | None = None  # the factor, when a discount applies


def _decimal(factor: Fraction) -> str:
    """6/5 -> '1.2' for the calculation trail, by integer division only."""
    whole, rest = divmod(factor.numerator * 10**6 // factor.denominator, 10**6)
    return f"{whole}.{rest:06d}".rstrip("0").rstrip(".") if rest else str(whole)


def unit_rate(rules: ContractRules, service: str, on, facility: str, tier: str, adj: Adjustments,
              trail: list[str] | None = None) -> int:
    """The effective unit rate, with each step appended to `trail` in words."""
    if adj.bundle:
        rate = rules.bundles[service][1]
        start = f"bundled with {rules.bundles[service][0]} {money(rate)}"
    else:
        rate = rules.rate_on(service, on)
        start = f"{'dated rate' if service in rules.rate_periods else 'base'} {money(rate)}"
    if trail is not None:
        trail.append(start)
    for factor, label in (
        (rules.facility_factors.get(service, {}).get(facility), f"facility {facility}"),
        (rules.tier_factors.get(service, {}).get(tier), f"tier {tier}"),
        (rules.threshold_premiums[service][1] if adj.premium else None, "threshold premium"),
        (rules.weekend_uplifts.get(service) if adj.weekend else None, "non-business day"),
        (adj.discount, "volume discount"),
    ):
        if factor is not None:
            rate = apply_factor(rate, factor)
            if trail is not None:
                trail.append(f"x{_decimal(factor)} {label} = {money(rate)}")
    return rate


def allowed_rates(rules: ContractRules, service: str, on, facility: str, tier: str) -> set[int]:
    """Every unit price the contract could legitimately produce for this line."""
    discounts = [None] + [f for _, f in rules.volume_discounts.get(service, [])]
    return {
        unit_rate(rules, service, on, facility, tier, Adjustments(b, p, w, d))
        for b, p, w, d in product(
            (False, service in rules.bundles),
            (False, service in rules.threshold_premiums),
            (False, service in rules.weekend_uplifts),
            discounts,
        )
    }


# Which single wrong adjustment turns the expected rate into the billed one.
_FLIPS = (
    ("bundle", "bundle_not_applied", "bundle_incorrectly_applied"),
    ("premium", "premium_omitted", "premium_incorrectly_applied"),
    ("weekend", "premium_omitted", "premium_incorrectly_applied"),
)


def _explain_price(rules, service, on, facility, tier, expected: Adjustments, billed: int) -> str:
    """Name the pricing error: which adjustment the hospital got wrong, if any."""
    available = {
        "bundle": service in rules.bundles,
        "premium": service in rules.threshold_premiums,
        "weekend": service in rules.weekend_uplifts,
    }
    for attr, when_expected, when_not in _FLIPS:
        if not available[attr]:
            continue
        flipped = Adjustments(**{**expected.__dict__, attr: not getattr(expected, attr)})
        if unit_rate(rules, service, on, facility, tier, flipped) == billed:
            return when_expected if getattr(expected, attr) else when_not
    # hospital_5: the price another facility or plan tier would pay.
    for other_facility, other_tier in product(
        rules.facility_factors.get(service, {facility: None}),
        rules.tier_factors.get(service, {tier: None}),
    ):
        if (other_facility, other_tier) == (facility, tier):
            continue
        if unit_rate(rules, service, on, other_facility, other_tier, expected) == billed:
            return "wrong_facility_or_tier_multiplier"
    other_discounts = [None] + [f for _, f in rules.volume_discounts.get(service, [])]
    for discount in other_discounts:
        if discount == expected.discount:
            continue
        trial = Adjustments(expected.bundle, expected.premium, expected.weekend, discount)
        if unit_rate(rules, service, on, facility, tier, trial) == billed:
            return "volume_discount_omitted" if expected.discount else "volume_discount_incorrectly_applied"
    return "unit_price_mismatch"


def price_lines(lines: pd.DataFrame, rules: ContractRules) -> pd.DataFrame:
    """Add expected unit rate, payable quantity, expected line total and findings.

    `lines` must already carry `service`, `match`, patient, facility, tier, dates.
    """
    df = lines.sort_values(["service_date", "line_id"], kind="stable", na_position="last").reset_index(drop=True)
    known = df["service"].notna()
    date_of = df["service_date"].dt.date.where(df["service_date"].notna(), None)

    # Who received what, when — for bundles and exclusion windows.
    delivered: dict[tuple, list] = defaultdict(list)
    for patient, service, day in zip(df["patient_id"][known], df["service"][known], df["service_date"][known]):
        delivered[(patient, service)].append(day)

    term_start, term_end = (pd.Timestamp(d) for d in rules.term)
    out_of_term = df["service_date"].notna() & ((df["service_date"] < term_start) | (df["service_date"] > term_end))
    not_yet_contracted = pd.Series(
        [bool(s in rules.available_from and d is not None and d < rules.available_from[s])
         for s, d in zip(df["service"], date_of)], index=df.index)

    duplicate = known & df.duplicated(["patient_id", "service", "service_date"], keep="first") & df["service_date"].notna()

    excluded = pd.Series(False, index=df.index)
    for i in df.index[known]:
        for window, other in rules.exclusions.get(df.at[i, "service"], []):
            day = df.at[i, "service_date"]
            if pd.notna(day) and any(
                pd.notna(d) and abs((day - d).days) <= window
                for d in delivered.get((df.at[i, "patient_id"], other), [])
            ):
                excluded[i] = True

    # Outside the term the contract sets no price, so none is invented: the line
    # is flagged and kept at its billed amount, like an unlisted service.
    payable = known & ~duplicate & ~excluded & ~not_yet_contracted & ~out_of_term
    day_key = list(zip(df["patient_id"], df["service"], df["service_date"]))
    day_total: dict[tuple, int] = defaultdict(int)
    for key, qty, ok in zip(day_key, df["quantity"], payable):
        if ok:
            day_total[key] += qty

    cumulative: dict[str, int] = defaultdict(int)
    used_today: dict[tuple, int] = defaultdict(int)
    exp_unit, exp_qty, price_finding, calculation = [], [], [], []

    for i, row in enumerate(df.itertuples(index=False)):
        service = row.service
        if not known[i]:
            exp_unit.append(pd.NA); exp_qty.append(row.quantity); price_finding.append("")
            calculation.append(f"no contracted service matched ({row.match}); billed amount kept")
            continue
        if not payable[i]:
            cumulative[service] += row.quantity
            if out_of_term[i] and not (duplicate[i] or excluded[i]):
                why = f"outside the contract term ({rules.term[0]} to {rules.term[1]}); billed amount kept"
            elif not_yet_contracted[i]:
                why = f"not contracted before {rules.available_from[service]}; billed amount kept"
            elif duplicate[i]:
                why = "repeat of the same service, patient and date; not payable"
            else:
                why = "within an exclusion window of " + ", ".join(
                    f"{other} ({window} days)" for window, other in rules.exclusions[service]) + "; not payable"
            exp_unit.append(0); exp_qty.append(0); price_finding.append(""); calculation.append(why)
            continue

        on = date_of[i]
        partner = rules.bundles.get(service, (None, None))[0]
        discount = None
        for threshold, factor in rules.volume_discounts.get(service, []):
            if cumulative[service] > threshold:
                discount = factor
        adj = Adjustments(
            bundle=partner is not None and row.service_date in delivered.get((row.patient_id, partner), []),
            premium=service in rules.threshold_premiums and day_total[day_key[i]] > rules.threshold_premiums[service][0],
            weekend=service in rules.weekend_uplifts and pd.notna(row.service_date) and row.service_date.weekday() >= 5,
            discount=discount,
        )
        trail: list[str] = []
        rate = unit_rate(rules, service, on, row.facility_code, row.plan_tier, adj, trail)
        cumulative[service] += row.quantity

        qty = row.quantity
        if service in rules.daily_caps:
            cap = rules.daily_caps[service]
            qty = max(0, min(qty, cap - used_today[day_key[i]]))
            used_today[day_key[i]] += row.quantity
            if qty < row.quantity:
                trail.append(f"qty {row.quantity} -> {qty} (daily cap {cap})")
        trail.append(f"x{qty} = {money(rate * qty)}")

        exp_unit.append(rate); exp_qty.append(qty); calculation.append(" | ".join(trail))
        price_finding.append(
            "" if row.unit_price_cents == rate
            else _explain_price(rules, service, on, row.facility_code, row.plan_tier, adj, row.unit_price_cents)
        )

    # Integer cents throughout; a line with no contracted service has no
    # expected unit rate, held as a missing value in a nullable integer column
    # (NaN would silently turn the whole column into floats).
    df["expected_unit_cents"] = pd.array(exp_unit, dtype="Int64")
    df["payable_quantity"] = exp_qty
    df["price_finding"] = price_finding
    df["calculation"] = calculation
    df["duplicate"], df["excluded"] = duplicate, excluded
    df["out_of_term"], df["not_yet_contracted"] = out_of_term, not_yet_contracted
    df["capped"] = known & payable & (df["payable_quantity"] < df["quantity"])
    # An unrecognised billing slug is a mismatch, not a reason to skip the check:
    # the first version skipped it, and missed every "per_hour_per_item" line.
    df["basis_mismatch"] = known & pd.Series(
        [rules.unit_basis.get(s) != (b if isinstance(b, str) else None)
         for s, b in zip(df["service"], df["contract_basis_billed"])],
        index=df.index,
    )

    # Unpriceable lines (unknown service, not yet contracted) keep their billed
    # amount: the label convention on hospital_1, and the honest default —
    # flag it, do not invent a price for it.
    unpriceable = ~known | not_yet_contracted | (out_of_term & ~duplicate & ~excluded)
    priced = df["expected_unit_cents"].fillna(0) * df["payable_quantity"].astype("Int64")
    df["expected_line_cents"] = priced.where(~unpriceable, df["line_total_cents"]).astype("int64")
    return df
