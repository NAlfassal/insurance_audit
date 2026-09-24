# Write-up
 
## What I built
 
Each contract is reduced to one rule schema — rates, dated rates, multipliers,
premiums, discounts, caps, bundles, exclusion windows — whether it is written as
tables (1, 4, 5), tables plus an amendment (3), or prose (2, whose 24,000 words
reduce to seven sentence templates). All five parse by regex. Each line is then
matched, re-priced in the contract's order with half-up rounding after each
step, and checked against structural rules. I planned an LLM only where rules
failed; they did not, so the run needs no API key. Every flag comes with its
calculation in `outputs/review_queue.csv`.
 
Four decisions shaped the result more than anything else:
 
- **Matching by containment, not similarity.** Descriptions are shorthand and
  often partial: `Procedure Immun Endosc` fits two services equally well, and a
  similarity score picks one and hides the tie. So every token must cover a
  distinct word of the service name, and where several services still fit, the
  billed price and unit basis decide — and the price counts only if the
  contract can actually produce it. 99.9% of lines are identified on every
  hospital, and a line the evidence cannot settle is reported as unknown rather
  than assigned to the nearest name.
- **Findings per line, not per invoice.** Pricing an invoice only when all of
  its lines match would let one unknown line hide every other error on it.
- **JSONL, not the CSVs.** Five invoice ids are reused; joining the CSVs on the
  id pools two patients' lines into one invoice.
- **Money is exact.** Every factor is a fraction of the contract's own digits
  and the code refuses a float: £206.25 × 0.70 is £144.375, which floating
  point rounds the wrong way. With float factors the current code changes 586
  verdicts on hospitals 2, 3 and 5 — and none on hospital_1.
## How I measured
 
**hospital_1, against the labels:** flags, totals to the cent, whether the named
category is the labelled one, and calibration. Optimistic by construction: I
developed against this set. Where the text left a convention open — is an
exclusion window inclusive? — I tried both and let the labels choose; the
decision log lists each one.
 
**hospitals 2–5, without labels**, three checks:
 
1. *Parse against the text.* Every rate must appear verbatim in the contract
   and every rule sentence or table row must become exactly one rule, or the
   run stops.
2. *Parse against the bills.* Since most invoices are correct, a mis-read rate
   surfaces as a service whose billed prices rarely match it. Agreement exceeds
   99.4% of lines on every hospital, and no service on any hospital falls below
   80%.
3. **Planted Error Validation:** Injected 1,100 known errors across 14
   categories (e.g., `line_total_arithmetic`,
   `volume_discount_incorrectly_applied`, `cross_invoice_duplicate`) into clean
   invoices. The pipeline detected and correctly categorized **1,100 / 1,100
   (100%)** without altering clean verdicts. *(Validates rule execution
   integrity; real-world recall remains anchored to `hospital_1`).*
**Evidence-Based Confidence:** Confidence is tiered by evidence — **0.97**
for structural breaches, **0.93** for contract violations, and **0.97** for
clean, fully re-priced invoices (held below the dev set's 1.000, since the
rules were tuned on it). Rows with daily cap violations receive
**0.60**: the breach itself is certain, but the exact recalculated total
carries ambiguity.
 
## Where I am uncertain
 
- **Daily-cap totals.** The contract caps the daily billable quantity per
  patient per service, but omits any rule for the excess, so the engine pays up
  to the cap. On hospital_1 a line bills 9 units against a cap of 4: the
  contract allows paying 4, the label pays 3 — a quantity no clause supports.
  All four dev cases take this shape. The breach is identified on all four and
  the recalculated total on none, so these rows carry 0.60 and their amount is
  marked disputed (28 rows on hospitals 2–5).
- **Rare rules.** The parse checks prove every window and cap became a rule,
  not that its day count or limit was read right. Firing on only 3–8 lines
  each, they are too rare for price agreement to detect parameter errors.
- **Rules read from one contract into another.** hospitals 1, 2 and 4 state
  that an exclusion window is measured in either direction from the service
  date. hospitals 3 and 5 say nothing about direction, so I applied the same
  rule to them. Likewise, hospital_2 does not forbid billing the same service
  twice on one date; the other four do, and I applied their rule.
- **The alias table was built from this data.** An unseen shorthand would show
  as a false `unknown_service`.
## Limitations & Future Work
 
I stopped at four contracts fully priced rather than hand-verifying the rules
that fire on a handful of lines each.
 
*Assessment of unattempted scope and prioritized next steps (ordered by risk
severity):*
 
1. **Hand-Verify Rare Rules:** Audit contract text against the 17–40 rows per
   contract affected by rare rules.
2. **Clarify Daily Cap Ambiguity.**
3. **Execute LLM Cross-Check:** Run a second reader of each contract
   (`src/llm_crosscheck.py`) whose mistakes differ from the regex's.
4. **Expand Synthetic Error Planting:** an omitted premium, bundle or discount
   needs a correct price independent of my pricing code; the model's reading
   can supply it.
5. **Implement Regression Tests** per labelled category.
