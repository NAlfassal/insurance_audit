# Prompts and AI assistance
 
## How I used AI
 
I used Claude as a pair programmer throughout: exploring the data, writing and
refactoring the pipeline, diagnosing where errors came from, and drafting
documentation.
 
The design decisions are mine: matching by containment and corroboration
rather than similarity, raising findings per line rather than per invoice,
settling open conventions against the labels, validating extraction on the
unlabelled hospitals by price agreement, and setting each confidence tier.
They are explained in `reports/writeup.md` and `reports/decision_log.md`.
 
## Where a model fits in the pipeline — and where it does not
 
My plan was rules first and a model only where rules failed. They did not
fail: all five contracts parse by regex, and on every hospital the parsed rules
reproduce over 99.4% of billed lines to the cent. So no prompt runs in the default
pipeline, and the submission reproduces without an API key.
 
Both prompts below were written for the two places I expected rules to fail.
One is still useful; one turned out to be unnecessary.
 
### `extract_base_rates_v1.txt` — used by `src/llm_crosscheck.py`
 
A model reads the contract text independently and returns the base-rate table.
The cross-check lists every service where it disagrees with the regex parser.
It is optional and was not needed to produce the submission; it is the second
reader I would run next (write-up, next steps).
 
Design decisions:
- **One entry per rate period, not per service.** hospital_3 reprices mid-term
  by amendment. Asking for one row per service silently loses the second rate.
- **Integer cents, stated with an example.** "GBP 1,701.25 -> 170125" removes the
  ambiguity that produces floats; a floating-point product can round a true
  half-cent the wrong way.
- **An explicit exclusion list.** Naming the clauses that do *not* set a rate
  (notices, audit rights, confidentiality, force majeure) matters most on
  hospital_2, where 22 of 35 articles are boilerplate and only 13 carry rates.
- **Adjustments are out of scope, on purpose.** Premiums and discounts go to
  `ambiguities`, not to `services`, so a base rate can never be confused with an
  adjusted one.
- **An asymmetric instruction on uncertainty:** omit rather than guess, and say
  why. An omission is a gap I can see; a wrong rate propagates into every
  invoice touching that service and looks correct.
- **Temperature 0 in the call.** The same contract must produce the same table
  on every run.
### `resolve_ambiguous_match_v1.txt` → `v2.txt` — iterated, then not needed
 
`v2` revises the confidence section of `v1`; the change is described below.
 
Written to resolve a billing description that matching could not settle. With a
similarity-based matcher that was a real gap. The matcher I settled on —
containment, then the line's own price and unit basis as tie-breakers — leaves
**no** line ambiguous on any hospital, so this prompt has nothing to do. I kept
it because the reasoning in it is what the matcher now does deterministically.
 
Design decisions:
- **Only ambiguous cases would reach it**, never the bulk of lines.
- **It is told not to re-rank on surface similarity**, which was tested and
  dropped (`reports/evaluation.md`).
- **Unit basis and price are given as corroborating evidence**, with the
  contract's real adjustment range. This is the idea that became the matcher's
  tie-break: a billed price that no allowed adjustment can produce means the
  wrong service, not a wrong invoice.
- **`runner_up` and `why_not_runner_up` are required**, so an answer is
  reviewable rather than just a label.
- **The match and its confidence are separate decisions.** `v1` listed "null"
  as if it were a fourth confidence level, which conflated *which service is
  it* with *how sure am I*. In `v2` a null is scored on the same scale as
  a name: a confident null is 0.9, an unsure one 0.5.
- **The cost of each failure is stated, and it is asymmetric.** A null costs one
  unpriced line; a wrong service mis-prices every invoice carrying that
  description.
- **It is told not to round up**, because the value is thresholded.
