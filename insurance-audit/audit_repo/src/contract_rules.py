"""
Reduce each hospital's contract to one machine-usable rule schema.

The five contracts are written five different ways, but they all express the
same mechanisms, so they all reduce to the same `ContractRules`:

  base rates + unit basis   the rate and billing unit per service
  rate periods              date-effective rates (hospital_3's amendment)
  facility / tier factors   multipliers by facility and plan tier (hospital_5)
  threshold premiums        uplift when a patient's daily quantity exceeds a bar
  non-business-day uplifts  uplift when the service date is a weekend
  volume discounts          discount once hospital-wide utilisation passes a bar
  daily caps                maximum billable units per patient per service day
  bundles                   substituted rates when a pair share a service day
  exclusion windows         service not billable near another service

Only the *reading* differs: markdown tables (hospital_1, hospital_4), tables
plus two `###` multiplier tables (hospital_5), base agreement + appendix +
amendment (hospital_3), and prose whose every rule is one of seven fixed
sentence templates with its numeral in brackets — "six (6)" (hospital_2).

Everything is regex over the contract's own text, which is regular enough for
a parser to be exact and repeatable. Extraction is checked twice: against the
text as it is parsed (`validate_contract` below — the run stops on any
disagreement), and against the bills by `validate_rules.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction
from pathlib import Path

DATA_DIR = Path("data")

# "GBP 1,301.25" -> 130125 cents. Money never becomes a float beyond this point.
MONEY = re.compile(r"GBP\s*([\d,]+\.\d{2})")

CONTRACT_FILES = {
    1: ["contracts/hospital_1/provider_services_agreement.md"],
    2: ["contracts/hospital_2/master_services_agreement.md"],
    3: [
        "contracts/hospital_3/base_agreement.md",
        "contracts/hospital_3/appendix_b_rate_schedule.md",
        "contracts/hospital_3/amendment_no_1.md",
    ],
    4: ["contracts/hospital_4/conditional_reimbursement_agreement.md"],
    5: ["contracts/hospital_5/network_reimbursement_agreement.md"],
}

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}


@dataclass
class ContractRules:
    """Every pricing mechanism in one contract, keyed by service name."""

    contract_number: str = ""
    term: tuple[date, date] | None = None
    base_rates: dict[str, int] = field(default_factory=dict)
    unit_basis: dict[str, str] = field(default_factory=dict)
    # service -> [(effective_from, effective_to, rate)], either end may be None
    rate_periods: dict[str, list[tuple[date | None, date | None, int]]] = field(default_factory=dict)
    # service -> first service date on which it is contracted at all
    available_from: dict[str, date] = field(default_factory=dict)
    # service -> {facility code: factor}; service -> {plan tier: factor}
    facility_factors: dict[str, dict[str, Fraction]] = field(default_factory=dict)
    tier_factors: dict[str, dict[str, Fraction]] = field(default_factory=dict)
    # service -> (daily quantity threshold, uplift factor)
    threshold_premiums: dict[str, tuple[int, Fraction]] = field(default_factory=dict)
    # service -> uplift factor on a non-business day
    weekend_uplifts: dict[str, Fraction] = field(default_factory=dict)
    # service -> [(cumulative threshold, discount factor)], ascending
    volume_discounts: dict[str, list[tuple[int, Fraction]]] = field(default_factory=dict)
    # service -> max units per patient per service day
    daily_caps: dict[str, int] = field(default_factory=dict)
    # service -> (partner service, substituted rate for this service)
    bundles: dict[str, tuple[str, int]] = field(default_factory=dict)
    # service -> [(window in days, service whose delivery excludes it)]
    exclusions: dict[str, list[tuple[int, str]]] = field(default_factory=dict)

    def rate_on(self, service: str, on: date | None) -> int:
        """Base rate for a service on a service date (amendments are by date)."""
        for start, end, rate in self.rate_periods.get(service, []):
            if on is None:
                continue
            if (start is None or on >= start) and (end is None or on <= end):
                return rate
        return self.base_rates[service]


# --- shared helpers --------------------------------------------------------


def _cents(amount: str) -> int:
    """'1,301.25' -> 130125, read digit by digit: no float is ever involved."""
    pounds, pence = amount.replace(",", "").split(".")
    return int(pounds) * 100 + int(pence)


def _uplift(percent) -> Fraction:
    """'+20%' -> exactly 6/5."""
    return Fraction(100 + int(percent), 100)


def _discount(percent) -> Fraction:
    """'10%' off -> exactly 9/10."""
    return Fraction(100 - int(percent), 100)


def _first_int(text: str) -> int | None:
    """First integer in a cell, preferring a bracketed numeral ("eighty (80)")."""
    if bracketed := re.search(r"\((\d+)", text):
        return int(bracketed.group(1))
    if plain := re.search(r"(\d+)", text):
        return int(plain.group(1))
    return None


def _factor(text: str) -> Fraction:
    """'0.85' -> exactly 17/20, straight from the contract's digits."""
    return Fraction(text.strip())


def _long_date(text: str) -> date:
    """'1 January 2025' or '31 December 2024'."""
    day, month, year = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text).groups()
    return date(int(year), MONTHS[month.lower()], int(day))


def _section(text: str, *keywords: str) -> str:
    """Body of the first heading (any level) whose title contains a keyword.

    Found by keyword, not by number: hospital_1 calls it "7. Cumulative Volume
    Discounts", hospital_4 "8. Discounts", hospital_3's appendix "B.1 Rates".
    The body runs to the next heading of any level, so hospital_5's `###`
    multiplier tables are not swept into its base-rate section.
    """
    for match in re.finditer(r"^#{2,3} (?P<title>.+?)$(?P<body>.*?)(?=^#|\Z)", text, re.S | re.M):
        title = match.group("title").lower()
        if any(keyword in title for keyword in keywords):
            return match.group("body")
    return ""


def _rows(section: str) -> list[list[str]]:
    """Data rows of the markdown tables in a section (header rows dropped)."""
    rows = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line) <= set("|-: "):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells[0].lower() in {"service", "service a"}:
            continue
        rows.append(cells)
    return rows


def _header(text: str, rules: ContractRules) -> None:
    rules.contract_number = re.search(r"\*\*Contract number:\*\*\s*(\S+)", text).group(1)
    start = _long_date(re.search(r"\*\*Effective from:\*\*\s*(.+)", text).group(1))
    end = _long_date(re.search(r"\*\*Effective to:\*\*\s*(.+)", text).group(1))
    rules.term = (start, end)


# --- table contracts (hospitals 1, 3, 4, 5) ---------------------------------


def _parse_tables(text: str, rules: ContractRules) -> None:
    # base rates — | Service | Unit basis | GBP x | cap? |
    for row in _rows(_section(text, "rate schedule", "base rate", "rates")):
        if len(row) < 3 or not (money := MONEY.search(row[2])):
            continue
        rules.base_rates[row[0]] = _cents(money.group(1))
        rules.unit_basis[row[0]] = row[1]
        if len(row) > 3 and (cap := _first_int(row[3])) is not None:
            rules.daily_caps[row[0]] = cap

    # multipliers (hospital_5) — | Service | F-MAIN | F-NORTH | F-COAST |
    for keyword, target in (("facility multiplier", rules.facility_factors),
                            ("plan-tier multiplier", rules.tier_factors)):
        body = _section(text, keyword)
        header = next((l for l in body.splitlines() if l.strip().startswith("| Service")), None)
        if header is None:
            continue
        columns = [c.strip() for c in header.strip().strip("|").split("|")][1:]
        for row in _rows(body):
            target[row[0]] = {col: _factor(val) for col, val in zip(columns, row[1:])}

    # threshold premiums — | Service | more than 6 visits | +20% |
    for row in _rows(_section(text, "threshold premium")):
        uplift = re.search(r"\+(\d+)%", row[-1]) if len(row) >= 3 else None
        if uplift and (threshold := _first_int(row[1])) is not None:
            rules.threshold_premiums[row[0]] = (threshold, _uplift(uplift.group(1)))

    # non-business-day uplifts — | Service | +20% |
    for row in _rows(_section(text, "non-business-day")):
        if len(row) >= 2 and (uplift := re.search(r"\+(\d+)%", row[1])):
            rules.weekend_uplifts[row[0]] = _uplift(uplift.group(1))

    # volume discounts — | Service | 60 nights | 10% |
    for row in _rows(_section(text, "volume discount", "discounts")):
        if len(row) < 3:
            continue
        threshold, discount = _first_int(row[1]), _first_int(row[2])
        if threshold is not None and discount is not None:
            rules.volume_discounts.setdefault(row[0], []).append((threshold, _discount(discount)))

    # daily caps — | Service | 6 days |
    for row in _rows(_section(text, "daily quantity")):
        if len(row) >= 2 and (cap := _first_int(row[1])) is not None:
            rules.daily_caps[row[0]] = cap

    # bundles — the money columns are located, because column order differs:
    #   | A | B | rate A | rate B |   and   | A | rate A | B | rate B |
    for row in _rows(_section(text, "bundled")):
        money_at = [i for i, c in enumerate(row) if MONEY.search(c)]
        name_at = [i for i, c in enumerate(row) if not MONEY.search(c) and c not in {"", "—"}]
        if len(money_at) != 2 or len(name_at) != 2:
            continue
        a, b = row[name_at[0]], row[name_at[1]]
        rules.bundles[a] = (b, _cents(MONEY.search(row[money_at[0]]).group(1)))
        rules.bundles[b] = (a, _cents(MONEY.search(row[money_at[1]]).group(1)))

    # exclusion windows — | Service | 7 days | Service that excludes it |
    for row in _rows(_section(text, "exclusion window")):
        if len(row) >= 3 and (window := _first_int(row[1])) is not None:
            rules.exclusions.setdefault(row[0], []).append((window, row[2]))


def _parse_amendment(text: str, rules: ContractRules) -> None:
    """hospital_3 Amendment No. 1: date-effective rates and new services.

    A1.1.2 says the amendment applies by *service date*, not invoice date, so
    each substituted service carries two rate periods split on that date.
    """
    effective = _long_date(re.search(r"takes effect on (.+?)\.", text).group(1))
    day_before = date.fromordinal(effective.toordinal() - 1)

    for row in _rows(_section(text, "substituted rates")):
        old, new = MONEY.search(row[2]), MONEY.search(row[3])
        rules.rate_periods[row[0]] = [
            (None, day_before, _cents(old.group(1))),
            (effective, None, _cents(new.group(1))),
        ]
        rules.unit_basis.setdefault(row[0], row[1])
        rules.base_rates.setdefault(row[0], _cents(old.group(1)))

    for row in _rows(_section(text, "additional services")):
        rules.base_rates[row[0]] = _cents(MONEY.search(row[2]).group(1))
        rules.unit_basis[row[0]] = row[1]
        rules.available_from[row[0]] = effective


# --- prose contract (hospital_2) --------------------------------------------

CLAUSE = re.compile(r"^\d+\.\d+ In respect of (?P<service>[^,]+), the Provider shall invoice the Payer "
                    r"at the rate of GBP (?P<rate>[\d,]+\.\d{2}) (?P<unit>per [^.]+?)\.(?P<rest>.*)$", re.M)
BRACKET = r"\((\d+)%?\)"


def _parse_prose(text: str, rules: ContractRules) -> None:
    for clause in CLAUSE.finditer(text):
        service = clause.group("service").strip()
        rules.base_rates[service] = _cents(clause.group("rate"))
        rules.unit_basis[service] = clause.group("unit").strip()
        rest = clause.group("rest")

        if m := re.search(r"not bill more than [^(]*" + BRACKET, rest):
            rules.daily_caps[service] = int(m.group(1))
        if m := re.search(r"aggregate quantity .*? exceeds [^(]*" + BRACKET + r".*? increased by [^(]*" + BRACKET, rest):
            rules.threshold_premiums[service] = (int(m.group(1)), _uplift(m.group(2)))
        if m := re.search(r"does not fall on a Business Day, .*? increased by [^(]*" + BRACKET, rest):
            rules.weekend_uplifts[service] = _uplift(m.group(1))
        for m in re.finditer(r"cumulative utilisation of this Service exceeds [^(]*" + BRACKET
                             + r".*? discount of [^(]*" + BRACKET, rest):
            rules.volume_discounts.setdefault(service, []).append((int(m.group(1)), _discount(m.group(2))))
        if m := re.search(r"Where this Service and (?P<partner>.+?) are both delivered .*? this Service at GBP "
                          r"(?P<own>[\d,]+\.\d{2})[^,]*and .*? at GBP (?P<other>[\d,]+\.\d{2})", rest):
            partner = m.group("partner").strip()
            rules.bundles[service] = (partner, _cents(m.group("own")))
            rules.bundles[partner] = (service, _cents(m.group("other")))
        for m in re.finditer(r"This Service is not billable where (?P<other>.+?) has been delivered "
                             r"to the same Patient within [^(]*" + BRACKET, rest):
            rules.exclusions.setdefault(service, []).append((int(m.group(2)), m.group("other").strip()))


# --- parse-time checks --------------------------------------------------------


class ContractParseError(ValueError):
    """The parsed rules disagree with the contract text they came from."""


def _money_text(cents: int) -> str:
    """130125 -> '1,301.25', the way the contracts print money."""
    return f"{cents // 100:,}.{cents % 100:02d}"


# hospital_2: how many sentences of each rule template the text contains, and
# how many rules the parser must therefore have produced from them.
PROSE_TEMPLATES = {
    "base rates": (r"shall invoice the Payer at the rate of GBP", lambda r: len(r.base_rates)),
    "daily caps": (r"The Provider shall not bill more than", lambda r: len(r.daily_caps)),
    "threshold premiums": (r"aggregate quantity of this Service", lambda r: len(r.threshold_premiums)),
    "weekend uplifts": (r"this Service does not fall on a Business Day", lambda r: len(r.weekend_uplifts)),
    "volume discount tiers": (r"cumulative utilisation of this Service exceeds",
                              lambda r: sum(len(t) for t in r.volume_discounts.values())),
    "bundle members": (r"Where this Service and .+? are both delivered", lambda r: len(r.bundles)),
    "exclusion windows": (r"This Service is not billable where",
                          lambda r: sum(len(w) for w in r.exclusions.values())),
}

# Table contracts: every data row in these sections must become exactly one rule.
TABLE_SECTIONS = {
    "threshold premiums": (("threshold premium",), lambda r: len(r.threshold_premiums)),
    "weekend uplifts": (("non-business-day",), lambda r: len(r.weekend_uplifts)),
    "volume discount tiers": (("volume discount", "discounts"),
                              lambda r: sum(len(t) for t in r.volume_discounts.values())),
    "exclusion windows": (("exclusion window",), lambda r: sum(len(w) for w in r.exclusions.values())),
}


def validate_contract(rules: ContractRules, texts: list[str], prose: bool) -> list[str]:
    """Check the parsed rules against the text they were read from.

    These are cheap, label-free checks that catch a parser silently dropping or
    inventing a rule — the failure a downstream accuracy number cannot see when
    the rule rarely fires:

      * every rate the rules use appears verbatim in the contract text;
      * every service a rule refers to is a contracted service;
      * every rule sentence (prose) or table row became exactly one rule;
      * every factor points the right way (uplifts > 1, discounts < 1).
    """
    text = "\n".join(texts)
    problems: list[str] = []

    rates = [(s, r) for s, r in rules.base_rates.items()]
    rates += [(s, r) for s, periods in rules.rate_periods.items() for _, _, r in periods]
    rates += [(s, r) for s, (_, r) in rules.bundles.items()]
    for service, cents in rates:
        if f"GBP {_money_text(cents)}" not in text:
            problems.append(f"rate {_money_text(cents)} for {service!r} is not in the contract text")

    known = set(rules.base_rates)
    referenced = {
        "facility factors": set(rules.facility_factors), "tier factors": set(rules.tier_factors),
        "premiums": set(rules.threshold_premiums), "weekend uplifts": set(rules.weekend_uplifts),
        "discounts": set(rules.volume_discounts), "caps": set(rules.daily_caps),
        "bundles": set(rules.bundles) | {p for p, _ in rules.bundles.values()},
        "exclusions": set(rules.exclusions) | {o for ws in rules.exclusions.values() for _, o in ws},
        "rate periods": set(rules.rate_periods), "added services": set(rules.available_from),
    }
    for kind, services in referenced.items():
        for service in sorted(services - known):
            problems.append(f"{kind} refer to {service!r}, which is not a contracted service")
    for service, (partner, _) in rules.bundles.items():
        if rules.bundles.get(partner, (None,))[0] != service:
            problems.append(f"bundle {service!r} -> {partner!r} is not symmetric")
    if set(rules.base_rates) != set(rules.unit_basis):
        problems.append("some services have a rate but no unit basis, or the reverse")

    if prose:
        for kind, (pattern, count) in PROSE_TEMPLATES.items():
            sentences = len(re.findall(pattern, text))
            if sentences != count(rules):
                problems.append(f"{kind}: {sentences} sentences in the text, {count(rules)} parsed")
    else:
        for kind, (keywords, count) in TABLE_SECTIONS.items():
            rows = sum(len(_rows(_section(t, *keywords))) for t in texts)
            if rows != count(rules):
                problems.append(f"{kind}: {rows} table rows in the text, {count(rules)} parsed")
        for kind, table in (("facility", rules.facility_factors), ("tier", rules.tier_factors)):
            if table and set(table) != known:
                problems.append(f"{kind} multipliers cover {len(table)} of {len(known)} services")

    factors = [("premium", f, 1) for _, f in rules.threshold_premiums.values()]
    factors += [("weekend uplift", f, 1) for f in rules.weekend_uplifts.values()]
    factors += [("discount", f, -1) for tiers in rules.volume_discounts.values() for _, f in tiers]
    for kind, factor, direction in factors:
        if (factor - 1) * direction <= 0:
            problems.append(f"{kind} factor {factor} points the wrong way")
    for table in (rules.facility_factors, rules.tier_factors):
        for service, by_key in table.items():
            if any(not 0 < f < 3 for f in by_key.values()):
                problems.append(f"multiplier for {service!r} is out of range: {by_key}")
    return problems


# --- entry point -------------------------------------------------------------


def load_contract(hospital_id: int, data_dir: Path = DATA_DIR) -> ContractRules:
    """Parse one hospital's contract documents into a ContractRules."""
    rules = ContractRules()
    paths = [data_dir / p for p in CONTRACT_FILES[hospital_id]]
    texts = [p.read_text(encoding="utf-8") for p in paths]
    _header(texts[0], rules)

    if hospital_id == 2:
        _parse_prose(texts[0], rules)
    else:
        for path, text in zip(paths, texts):
            if "amendment" in path.name:
                _parse_amendment(text, rules)
            else:
                _parse_tables(text, rules)

    for thresholds in rules.volume_discounts.values():
        thresholds.sort()

    if problems := validate_contract(rules, texts, prose=hospital_id == 2):
        raise ContractParseError(f"hospital_{hospital_id}: " + "; ".join(problems))
    return rules


def rules_to_dict(rules: ContractRules) -> dict:
    """The rules as plain JSON, for a reviewer to read against the contract.

    Money stays in integer cents next to its printed form; factors are written
    as the exact decimal the contract states ("1.2", "0.85"), never a float.
    """
    def money(cents: int) -> dict:
        return {"cents": cents, "text": f"GBP {_money_text(cents)}"}

    def factor(f: Fraction) -> str:
        whole, rest = divmod(f.numerator * 10**6 // f.denominator, 10**6)
        return f"{whole}.{rest:06d}".rstrip("0").rstrip(".") if rest else str(whole)

    iso = lambda d: d.isoformat() if d else None
    services = {}
    for name in sorted(rules.base_rates):
        entry: dict = {"unit_basis": rules.unit_basis.get(name), "base_rate": money(rules.base_rates[name])}
        if name in rules.rate_periods:
            entry["rate_periods"] = [{"from": iso(a), "to": iso(b), "rate": money(r)}
                                     for a, b, r in rules.rate_periods[name]]
        if name in rules.available_from:
            entry["contracted_from"] = iso(rules.available_from[name])
        if name in rules.facility_factors:
            entry["facility_multipliers"] = {k: factor(v) for k, v in rules.facility_factors[name].items()}
        if name in rules.tier_factors:
            entry["plan_tier_multipliers"] = {k: factor(v) for k, v in rules.tier_factors[name].items()}
        if name in rules.threshold_premiums:
            bar, f = rules.threshold_premiums[name]
            entry["threshold_premium"] = {"when_daily_quantity_exceeds": bar, "factor": factor(f)}
        if name in rules.weekend_uplifts:
            entry["non_business_day_factor"] = factor(rules.weekend_uplifts[name])
        if name in rules.volume_discounts:
            entry["volume_discounts"] = [{"when_cumulative_exceeds": t, "factor": factor(f)}
                                         for t, f in rules.volume_discounts[name]]
        if name in rules.daily_caps:
            entry["daily_cap"] = rules.daily_caps[name]
        if name in rules.bundles:
            partner, rate = rules.bundles[name]
            entry["bundle"] = {"with": partner, "rate": money(rate)}
        if name in rules.exclusions:
            entry["not_billable_within"] = [{"days": w, "of": other} for w, other in rules.exclusions[name]]
        services[name] = entry
    return {
        "contract_number": rules.contract_number,
        "term": {"from": iso(rules.term[0]), "to": iso(rules.term[1])},
        "services": services,
    }


def summarise(rules: ContractRules) -> str:
    return (
        f"{rules.contract_number} | {len(rules.base_rates)} services | "
        f"{len(rules.rate_periods)} dated rates | {len(rules.available_from)} added services | "
        f"{len(rules.facility_factors)}/{len(rules.tier_factors)} facility/tier factor rows | "
        f"{len(rules.threshold_premiums)} premiums | {len(rules.weekend_uplifts)} weekend uplifts | "
        f"{len(rules.volume_discounts)} discounts | {len(rules.daily_caps)} caps | "
        f"{len(rules.bundles)} bundle members | {len(rules.exclusions)} exclusions"
    )


if __name__ == "__main__":
    for hospital_id in CONTRACT_FILES:
        print(f"hospital_{hospital_id}: {summarise(load_contract(hospital_id))}")
