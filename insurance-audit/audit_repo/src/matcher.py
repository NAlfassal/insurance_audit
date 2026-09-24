"""
Map a free-text billing line to exactly one contracted service, or to none.

Descriptions are shorthand, word-shuffled and often partial: "Procedure Immun
Endosc" fits both Ambulatory and Preoperative Immunologic Endoscopic Procedure,
and a similarity score picks the nearest and hides the tie. So three stages:

  1. containment  — every token must cover a distinct word of the contract name
                    (prefix or known alias). One candidate -> matched.
  2. corroborate  — several left: keep those whose price, under an adjustment
                    the contract allows, equals the billed price; then those
                    whose unit basis agrees. One left -> matched.
  3. give up      — still ambiguous: left unmatched, not assigned to the
                    nearest name.

A description covering no contracted service is a finding in its own right.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import product

from contract_rules import ContractRules
from pricing import allowed_rates

# Supplier codes ("/NG-3022", "/SA-8184") carry no service meaning.
TRAILING_CODE = re.compile(r"/[A-Z]{2}-\d+")

# Abbreviations that are NOT a prefix of the word they stand for. Anything
# that is a prefix ("Ophth" -> ophthalmic, "Recov" -> recovery) needs no entry:
# prefix coverage handles it, including genuinely ambiguous ones like "Endo".
ALIASES: dict[str, set[str]] = {
    "asst": {"assisted"},
    "cr": {"care"},
    "cs": {"case"},
    "bd": {"bed"},
    "ent": {"otolaryngologic"},
    "gi": {"gastrointestinal"},
    "hm": {"home"},
    "img": {"imaging"},
    "inpt": {"inpatient"},
    "msk": {"musculoskeletal"},
    "outpt": {"outpatient"},
    "plng": {"planning"},
    "pnl": {"panel"},
    "rm": {"room"},
    "rtn": {"routine"},
    "spclst": {"specialist"},
    "spcm": {"specimen"},
    "anly": {"analysis"},
    "std": {"standard"},
    "supv": {"supervised"},
    "svc": {"service"},
    "thtr": {"theatre"},
    "tm": {"time"},
    "vst": {"visit"},
    "wd": {"ward"},
    "wnd": {"wound"},
}

# Billing systems write the unit basis as a slug; contracts write it in prose.
UNIT_BASIS = {
    "per_day": "per day of service",
    "per_hour": "per hour",
    "per_hour_per_item": "per hour, per item",
    "per_item": "per item supplied",
    "per_night": "per night of occupancy",
    "per_procedure": "per procedure",
    "per_test": "per test",
    "per_unit_dispensed": "per unit dispensed",
    "per_visit": "per visit",
}


def tokenize(description: str) -> list[str]:
    text = TRAILING_CODE.sub(" ", description)
    return [t for t in re.split(r"[^a-z]+", text.lower()) if t]


def _covers(token: str, word: str) -> bool:
    return word.startswith(token) or word in ALIASES.get(token, ())


def contained(tokens: list[str], words: list[str]) -> bool:
    """True if each token covers a *distinct* word of the contract name.

    Distinctness matters: "Immun Immun" must not match a name with a single
    "Immunologic". Names are at most five words, so a small search is exact.
    """
    options = [[i for i, w in enumerate(words) if _covers(t, w)] for t in tokens]
    if any(not o for o in options):
        return False
    return any(len(set(choice)) == len(choice) for choice in product(*options))


@dataclass(frozen=True)
class Match:
    service: str | None   # the contracted service, or None
    how: str              # "text" | "corroborated" | "ambiguous" | "unknown"
    n_candidates: int


class ServiceMatcher:
    """Match billing lines to one contract's services."""

    def __init__(self, rules: ContractRules):
        self.rules = rules
        self.names = list(rules.base_rates)
        self.words = {name: name.lower().split() for name in self.names}
        self._candidates: dict[str, list[str]] = {}
        self._rates: dict[tuple, set[int]] = {}

    def rates(self, service: str, on, facility: str, tier: str) -> set[int]:
        """Allowed unit prices for this service in this line's context.

        Context matters: hospital_5 multiplies by facility and plan tier, and
        hospital_3's amendment changes rates by service date.
        """
        dated = on if service in self.rules.rate_periods else None
        key = (service, dated, facility, tier)
        if key not in self._rates:
            self._rates[key] = allowed_rates(self.rules, service, on, facility, tier)
        return self._rates[key]

    def candidates(self, description: str) -> list[str]:
        if description not in self._candidates:
            tokens = tokenize(description)
            self._candidates[description] = [
                name for name in self.names if tokens and contained(tokens, self.words[name])
            ]
        return self._candidates[description]

    def match(self, description: str, unit_basis: str, unit_price: int,
              on=None, facility: str = "", tier: str = "") -> Match:
        cands = self.candidates(description)
        if not cands:
            return Match(None, "unknown", 0)

        basis = UNIT_BASIS.get(unit_basis)
        if len(cands) == 1:
            only = cands[0]
            # One disagreement is a billing error on a known service; two
            # (price AND basis) means it is not that service at all.
            if unit_price not in self.rates(only, on, facility, tier) and self.rules.unit_basis.get(only) != basis:
                return Match(None, "unknown", 1)
            return Match(only, "text", 1)
        by_price = [c for c in cands if unit_price in self.rates(c, on, facility, tier)]
        by_basis = [c for c in cands if self.rules.unit_basis.get(c) == basis]

        # Price is the stronger witness, so it goes first; basis narrows a
        # price tie, or stands alone when no candidate's price fits.
        pools = [by_price, [c for c in by_price if c in by_basis]] if by_price else [by_basis]
        for pool in pools:
            if len(pool) == 1:
                return Match(pool[0], "corroborated", len(cands))
        return Match(None, "ambiguous", len(cands))
