# Evaluation report
 
Two kinds of evidence, because only one hospital has labels:
 
- **hospital_1 (labelled)** — every figure below is reproduced by
  `python src/evaluate.py`. It is also the set I developed against, so these
  numbers are optimistic by construction.
- **hospitals 2–5 (unlabelled)** — checked three ways that need no labels:
  the parsed rules against the contract text (`cli.py rules`), the parsed rules
  against what the hospital billed (`cli.py validate`), and known errors planted
  into clean invoices (`cli.py inject`).
## hospital_1 — invoice-level flagging
 
| | value |
|---|---|
| Precision | **1.000** |
| Recall | **1.000** |
| TP / FP / FN / TN | 58 / 0 / 0 / 855 |
| Structural checks alone | precision 1.000, recall 0.448 (26 of 58) |
 
Recall is complete because findings are raised per line. Had an invoice been
priced only when all of its lines match, one unidentified line would have
hidden every other error on that invoice.
 
## hospital_1 — expected totals
 
Exact to the cent on **909 of 913** invoices, and on all 855 correct ones.
 
The four misses are all daily-cap invoices, and I could not reconcile them with
the contract text. On `INV-H1-000015` a single line bills 9 units of a service
capped at 4 per patient per day; the contract pays 4, the label pays 3. The
other three follow the same pattern. No other line for that patient and service
falls on those days, so I left the engine following the text rather than
fitting it to four rows. The flag and the category are right on all four; only
the amount differs.
 
| invoice | label | mine |
|---|---|---|
| INV-H1-000015 | 1,195,825 | 1,210,600 |
| INV-H1-000049 | 2,706,610 | 2,732,035 |
| INV-H1-000227 | 5,299,500 | 5,375,775 |
| INV-H1-000725 | 1,624,750 | 1,812,750 |
 
## hospital_1 — per category
 
The error named is the labelled one on every category, and the full category
set matches the label exactly on **58 of 58** flagged invoices.
 
| category | labelled | named | | category | labelled | named |
|---|---|---|---|---|---|---|
| unknown_service | 12 | 12 | | daily_cap_exceeded | 4 | 4 |
| wrong_unit_basis | 11 | 11 | | exclusion_window_violation | 4 | 4 |
| unit_price_mismatch | 10 | 10 | | cross_invoice_duplicate | 4 | 4 |
| premium_incorrectly_applied | 6 | 6 | | volume_discount_omitted | 4 | 4 |
| malformed_service_date | 6 | 6 | | volume_discount_incorrectly_applied | 4 | 4 |
| line_total_arithmetic | 6 | 6 | | premium_omitted | 3 | 3 |
| invoice_total_mismatch | 6 | 6 | | contract_number_mismatch | 5 | 5 |
| service_date_out_of_window | 5 | 5 | | duplicate_invoice_id | 5 | 5 |
| service_date_after_invoice_date | 5 | 5 | | bundle_not_applied | 5 | 5 |
 
## hospital_1 — calibration
 
| evidence | confidence | n | accuracy |
|---|---|---|---|
| structural breach | 0.97 | 26 | 1.000 |
| line contradicts contract | 0.93 | 28 | 1.000 |
| flagged, total disputed (daily cap) | 0.60 | 4 | flag 1.000, total 0.000 |
| clean, fully re-priced | 0.97 | 855 | 1.000 |
 
The flag tiers are right every time on the dev set, so the values I publish sit
deliberately below that. A perfect score on the set I tuned on does not justify
1.00 on four contracts I did not; line findings sit lowest because they depend
most on what was tuned.
 
The task asks for confidence in the *row*, and a row asserts an expected total
as well as a flag. The two disagree on one kind of invoice: where a daily cap
applies, the flag is right 4 of 4 and the total 0 of 4. Those rows get 0.60,
and I score calibration both ways:
 
| Brier | flag only | flag and total |
|---|---|---|
| all rows at their evidence tier | 0.0010 | 0.0048 |
| **capped rows at 0.60 (chosen)** | 0.0017 | **0.0026** |
 
I do not know which the scorer uses. Lowering the capped rows costs little if
it scores the flag alone, halves the error if it scores the whole row, and
states where I am unsure. On hospitals 2–5, 28 rows are in this tier, marked
`disputed` in
`outputs/audit_detail.csv`.
 
## hospitals 2–5 — the parse against the contract text
 
Before anything is priced, the parsed rules are checked against the text they
came from, and the run stops if any check fails:
 
- every rate used — base, dated and bundled — appears verbatim in the contract;
- every service a rule names (bundle partner, excluding service, multiplier
  row) is a contracted service;
- every rule sentence in hospital_2 (seven templates) and every table row
  elsewhere became exactly one rule;
- every uplift raises the price and every discount lowers it.
All five contracts pass. Tests confirm the checks bite: rewording one discount
sentence in hospital_2, or dropping one exclusion row, fails the parse.
`rules/hospital_N.json` holds the result, readable against each contract.
 
## hospitals 2–5 — extraction check
 
About 93% of invoices are correct, so a wrongly extracted rate shows up as a
service whose billed prices almost never match it. Every service is scored by
how often the billed unit price equals the price the parsed rules produce.
 
| hospital | lines | identified | ambiguous | price agreement | services below 80% |
|---|---|---|---|---|---|
| 1 | 11,415 | 99.89% | 0 | 99.67% | 0 of 108 |
| 2 | 14,360 | 99.90% | 0 | 99.64% | 0 of 76 |
| 3 | 11,655 | 99.88% | 0 | 99.54% | 0 of 120 |
| 4 | 10,560 | 99.88% | 0 | 99.55% | 0 of 98 |
| 5 | 13,221 | 99.91% | 0 | 99.48% | 0 of 84 |
 
The unlabelled hospitals score as well as the labelled one. This check is also
the only one that catches a rounding error. Replacing the exact fractions with
floating-point factors drops four services below the threshold: in binary,
20625 × 0.7 is 14437.4999…, so a true half-cent rounds down instead of up.
 
Flag rates are consistent too: 6.4% on hospital_1 against 6.8%, 7.5%, 7.5%
and 7.2% on hospitals 2–5, split roughly evenly between structural and
contract findings, as on the dev set.
 
## hospitals 2–5 — planted errors
 
The two checks above test the rules. This one tests the pipeline end to end on
the four contracts the submission is scored on. For each error type, one known
error is planted into each of 20 invoices per hospital that the pipeline passes
as clean (different patients, so planted errors cannot interact), and the
hospital is re-audited.
 
| planted error | planted | found | named | amount exact |
|---|---|---|---|---|
| unit_price_mismatch | 80 | 80 | 80 | 80 / 80 |
| line_total_arithmetic | 80 | 80 | 80 | 80 / 80 |
| invoice_total_mismatch | 80 | 80 | 80 | 80 / 80 |
| wrong_unit_basis | 80 | 80 | 80 | 80 / 80 |
| unknown_service | 80 | 80 | 80 | 80 / 80 |
| daily_cap_exceeded | 80 | 80 | 80 | — |
| premium_incorrectly_applied | 60 | 60 | 60 | 60 / 60 |
| volume_discount_incorrectly_applied | 80 | 80 | 80 | 80 / 80 |
| contract_number_mismatch | 80 | 80 | 80 | 80 / 80 |
| malformed_service_date | 80 | 80 | 80 | — |
| service_date_out_of_window | 80 | 80 | 80 | 80 / 80 |
| service_date_after_invoice_date | 80 | 80 | 80 | — |
| cross_invoice_duplicate | 80 | 80 | 80 | 80 / 80 |
| duplicate_invoice_id | 80 | 80 | 80 | — |
 
**1,100 of 1,100 found and correctly named**, 280 / 280 / 260 / 280 on
hospitals 2–5 (hospital_4 has no non-business-day uplifts to misapply), and no
untouched invoice changed verdict.
 
What this does and does not show:
 
- It shows each check fires on each contract's own services, shorthand and
  dates, not only on hospital_1's.
- It does not show recall on errors I did not think to plant. The planted
  errors are ones I wrote, so they are the kind the pipeline was built to find.
- An error is planted only when its correct total is known without using the
  pricing code under test. Charging an uplift or a discount on a plain line
  qualifies. An *omitted* premium, discount or bundle does not: working out the
  right price would mean re-deriving the very rule being tested. Those types
  are measured on hospital_1 only.
- "—" marks types where the true amount is itself a judgement (a cap, a
  malformed date) and is not scored.
It is also the only check that tests the out-of-term convention end to end: an
out-of-term line must keep its billed amount, not be re-priced at contract
rates. On the real data both readings give identical totals, so nothing in the
submission depends on it; a test pins the behaviour anyway.
 
## Where the approach can still go wrong
 
Nothing fails on the dev set, so these are the systematic risks rather than
observed misses — the places a wrong answer would come from.
 
### 1. The extraction check cannot see rules that rarely fire
Price agreement tests a rule only as often as the rule fires.
Base rates, multipliers and discounts apply to thousands of lines; exclusion
windows fire on 3–8 lines per hospital, caps on 4–8, duplicates on 4–7. A
mis-extracted window length or cap would disagree on a handful of lines and
never approach the 80% threshold. *Example:* hospital_3 has 10 exclusion rules
and 3 excluded lines — the check says nothing about the other 7 rules. The
parse-time checks now guarantee no such rule is dropped or invented, and that
it names real services; they do not check that its day count or cap is read
correctly. These rows are few enough to verify by hand against
`rules/hospital_N.json`, which is the first thing I would do.
 
### 2. Conventions settled on hospital_1 are applied to every contract
Four choices the text leaves open were settled by the hospital_1 labels and
applied to every contract: an exclusion window includes day N; an out-of-term
date is flagged but not zeroed; an unknown service keeps its billed amount; a
reused invoice id keeps the later record's totals. Two rules are also carried
into contracts that do not state them: the exclusion window runs in both
directions on hospitals 3 and 5, and a repeat billing is an error on
hospital_2. *Example:* if the windows on hospitals 3 and 5 ran backwards only,
9 excluded lines would become payable.
 
### 3. The matcher's alias table was built from the data I evaluate on
Containment needs a short list of abbreviations that are not prefixes of their
word (`Asst`, `Wnd`, `Pnl`, …). I built it by reading unidentified lines on the
clean invoices until none were left. A shorthand that appears only in hospitals
2–5 would be reported as `unknown_service` — a false alarm, not a silent miss.
*Example:* `Pnl` (panel) was missing at first and made 114 correct lines look
like unknown services; one alias fixed all of them. On hospitals 2–5 the
unknowns are 12–14 per hospital, in line with hospital_1's 12 genuine ones.
 
### 4. One matching rule was written for a single case
When a description matches exactly one service on its words, but the billed
price *and* the billed unit basis both contradict that service, I treat it as
an unknown service. The rule came from one hospital_1 line — a radiotherapy
fraction billed per visit at £82.25 — and fires nowhere in hospitals 2–5, so it
does not affect the submission; but a line with a genuine price error *and* a
genuine basis error on a known service would be misnamed by it.
 
## What the submission claims
 
3,942 rows, 285 flagged (7.2%).
 
| hospital | rows | flagged |
|---|---|---|
| hospital_2 | 1,125 | 76 |
| hospital_3 | 932 | 70 |
| hospital_4 | 835 | 63 |
| hospital_5 | 1,050 | 76 |
 
`expected_total_cents` is asserted on every row. Unknown services are kept at
their billed amount — flagged, not repriced — which is the hospital_1 label
convention and the honest default for a service the contract does not price.
 
## Approaches tried and dropped
 
- **Fuzzy similarity for matching** — picks one of two equal candidates and
  hides the tie, and a high score is no evidence the billed price is reachable
  under the contract. Re-running hospital_1 with a similarity-first matcher
  (abbreviations expanded, best score above 0.60 wins) gives precision 0.075
  against 1.000, 714 clean invoices wrongly flagged, expected totals exact on
  505 of 913 against 909, and price agreement 94.4% against 99.7%. It also
  assigns a plausible service to 7 of the 12 lines whose service the contract
  does not list — with a *high* score, so a "low score → ask a model" fallback
  would never see them. Replaced by word containment plus price and basis.
- **Pricing only fully matched invoices** — on hospital_1, 8 of the 12 invoices
  with an unknown service also carry a contract error this approach would never
  see.
- **Base-rate-only comparison** — precision 0.125: premiums and discounts move
  prices legitimately.
- **A classifier trained on hospital_1** — the label depends on the contract,
  and the task asks for an exact total.
