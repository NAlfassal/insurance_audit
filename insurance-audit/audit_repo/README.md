# Invoice audit — Meridian Health Assurance Group
 
Audits hospital invoices against their service contracts and produces one
verdict per invoice with a calibrated confidence.
 
**On the development set** (hospital_1, 913 invoices):
 
| | |
|---|---|
| Precision / recall | 1.000 / 1.000 |
| Expected total, exact to the cent | 909 / 913 |
| Error category matches the label | 58 / 58 |
| Brier — flag · flag and total | 0.0017 · 0.0026 |
 
**Without labels** — the submission covers hospitals 2–5, which have none, so
extraction is checked two other ways:
 
- every parsed rate appears verbatim in its contract, and every rule sentence
  or table row became exactly one rule, or the run stops
- over 99.4% of billed lines on every hospital reproduce the parsed rate to
  the cent
- 14 error types planted into clean invoices: 1,100 of 1,100 found and
  correctly named, no other invoice's verdict disturbed
`outputs/submission.csv` — 3,942 rows, 285 flagged (7.2%). No LLM call runs in
the pipeline; it is pure Python and reproducible.
 
The dev-set figures are optimistic by construction: the matcher and the pricing
conventions were developed against hospital_1. `reports/evaluation.md` sets out
what was checked on the unlabelled hospitals instead, and how.
 
## Installation & Setup
 
Dependencies are pinned in `pyproject.toml` and `requirements.txt`. Requires **Python 3.11+**.
 
```bash
git clone https://github.com/NAlfassal/insurance_audit.git
cd insurance_audit/insurance-audit/audit_repo
```
 
### Standard Setup 
 
**Linux / macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
**Windows:**
```bash
# Command Prompt
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
# PowerShell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
### Recommended Setup (With uv)
> **Tip:** I highly recommend using uv. It is significantly faster at resolving and installing Python dependencies than standard pip, and automatically manages project environments.
```bash
uv sync                                  
```
## Reproduce
Execute the pipeline and generate evaluation metrics:
```bash
# Using standard virtual environment
PYTHONPATH=src python src/cli.py all             # everything below, in order (~1 min)
 
PYTHONPATH=src python src/cli.py rules           # parse + check contracts, write rules/hospital_N.json
PYTHONPATH=src python src/cli.py validate        # extraction check on all five hospitals
PYTHONPATH=src python src/cli.py submit          # outputs/submission.csv, audit_detail.csv, review_queue.csv
PYTHONPATH=src python src/cli.py dev             # reproduces every hospital_1 number in reports/
PYTHONPATH=src python src/cli.py inject          # planted-error recall on hospitals 2-5
PYTHONPATH=src python src/evaluate.py --layer1   # scores the structural checks alone
PYTHONPATH=src python -m pytest -q               # regression tests
 
# Using uv
PYTHONPATH=src uv run python src/cli.py all
PYTHONPATH=src uv run python -m pytest -q
```
 
On Windows PowerShell, set the path once with `$env:PYTHONPATH="src"` and drop
the prefix from each command.
 
## How an invoice is audited
 
**1. Load** (`src/loader.py`) — from the nested JSONL, not the two CSVs. Line
items carry only `invoice_id`, and some ids are reused, so joining the CSVs pools
two invoices — often two patients — and corrupts every per-patient rule.
 
**2. Contract rules** (`src/contract_rules.py`) — each contract is reduced to one
schema: base rates and unit basis, date-effective rates, facility and plan-tier
multipliers, threshold premiums, non-business-day uplifts, volume discounts,
daily caps, bundles, exclusion windows. The five contracts are read three ways —
markdown tables (hospitals 1, 4, 5), tables plus an amendment (hospital_3), and
seven fixed sentence templates (hospital_2) — all by regex over the contract's own
text. Percentages and multipliers are kept as exact fractions of the contract's
own digits (+20% → 6/5, 0.85 → 17/20), never floats.
 
The parse checks itself against the text before anything is priced, and stops
if any check fails: every rate it uses appears verbatim in the contract; every
service a rule names is a contracted one; every rule sentence (hospital_2) or
table row became exactly one rule; every uplift raises and every discount
lowers. `cli.py rules` writes the result to `rules/hospital_N.json`, so the
rules can be read against the contract without running any code.
 
**3. Matching** (`src/matcher.py`) — billing descriptions are shorthand,
shuffled and often partial (`Procedure Immun Endosc` drops the qualifier). A
line matches a service when every description token covers a distinct word of
its name. Where several services still fit, the billed price and unit basis
decide; where they cannot, the line is left unidentified. On every hospital,
99.9% of lines are identified and none is left ambiguous.
 
**4. Re-pricing** (`src/pricing.py`) — the adjustments apply in the order the
contracts mandate (base or bundled rate → facility → plan tier → threshold
premium → non-business-day uplift → volume discount), rounding half-up after
each step in exact arithmetic. Money is integer cents from end to end — read
from the contract digit by digit, multiplied only by exact fractions, and
written as integers — and a float factor is refused outright. Daily totals, caps, cumulative utilisation,
bundles, exclusion windows and duplicates are evaluated hospital-wide, in
service-date then line-id order. Every line keeps its calculation in words
(`base 87.25 | x1.2 non-business day = 104.70 | x18 = 1884.60`), so any expected
figure can be redone by hand.
 
**5. Verdict** (`src/audit.py`) — findings are raised per line and per invoice
(`src/layer1_structural.py` for the checks that need no contract). An invoice
is flagged when any finding fires, or when its re-priced total differs from the
bill.
 
**6. Extraction check** (`src/validate_rules.py`) — most invoices are correct,
so a mis-extracted rate shows up as a service whose billed prices almost never
match it. Every service on every hospital is scored this way; any below 80%
agreement is listed for review. None is.
 
**7. Planted errors** (`src/inject_errors.py`) — the labels only cover
hospital_1, so recall on hospitals 2–5 is measured by planting known errors
into invoices the pipeline passes as clean, one type at a time, and checking
each is found, named, and priced correctly. An error is planted only when its
correct total is known without using the pricing code under test; results are in
`outputs/injection_recall.csv` and `reports/evaluation.md`.
 
## Outputs
 
| file | what it is for |
|---|---|
| `outputs/submission.csv` | the submission, in the template's columns |
| `outputs/audit_detail.csv` | the same rows, plus the evidence tier behind each confidence and whether the amount is disputed |
| `outputs/review_queue.csv` | every offending line of every flagged invoice: billed against expected, with the calculation |
| `outputs/injection_recall.csv` | planted-error results, per hospital and error type |
| `rules/hospital_N.json` | each contract's parsed rules, for reading against the contract |
 
## Confidence
 
Tiered by the evidence behind each row:
 
| value | rows | meaning | accuracy on dev |
|---|---|---|---|
| 0.97 | 133 | structural breach: the invoice contradicts itself | 1.000 (26) |
| 0.93 | 124 | a line contradicts its contract | 1.000 (28) |
| 0.60 | 28 | flagged, but the expected total rests on a disputed cap reading | flag 1.000, total 0.000 (4) |
| 0.97 | 3,657 | clean: every line identified and re-priced to the cent | 1.000 (855) |
 
Every tier scores 1.000 on the dev set, but the rules were developed there, so
each sits below 1 in proportion to how much it relies on what was tuned on
hospital_1. Line
findings sit lowest: they rest on matching and on conventions settled against
the hospital_1 labels, and on hospitals 2–5 they are checked only by price
agreement and planted errors, not by labels.
 
The task asks for confidence in the *row*, and a row also asserts an expected
total. On invoices with a daily-cap line the flag is certain but the total is
not: it rests on a cap reading the dev labels contradict on all four of their
capped invoices. Those rows get 0.60, and `audit_detail.csv` marks their amount
`disputed`. Scored on the flag alone this costs a little (Brier 0.0010 → 0.0017);
scored on flag and total together it halves the error (0.0048 → 0.0026).
 
## Layout
 
```
src/
  loader.py                 invoices and their own lines, from the JSONL
  contract_rules.py         contract -> one rule schema (tables, amendment, prose)
  matcher.py                billing line -> contracted service, or none
  pricing.py                re-prices each line in the mandated order
  layer1_structural.py      checks that need no contract
  audit.py                  one verdict per invoice, and the review queue
  evaluate.py               scores the pipeline against the hospital_1 labels
  validate_rules.py         extraction check for the unlabelled hospitals
  inject_errors.py          planted-error recall for the unlabelled hospitals
  build_submission.py       writes the submission, audit detail and review queue
  cli.py                    one entry point: rules | validate | submit | dev | inject | all
  llm_crosscheck.py         optional: a model as a second reader of each contract
rules/                      each contract's parsed rules as JSON
tests/                      regression tests for the failures found along the way
prompts/                    versioned prompts, with notes on how each was designed
reports/                    writeup.md, evaluation.md, decision_log.md
data/                       the exercise package, unchanged
```
 
## Use of AI assistance
 
See `prompts/README.md` — it covers how AI was used, the versioned prompts, and
the design decisions behind each. No prompt runs in the default pipeline, so the
submission reproduces without an API key.
