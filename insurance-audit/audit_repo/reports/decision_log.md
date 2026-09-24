# Decision log

## Two traps worth naming

- **hospital_5's multiplier tables sit under `###` headings**, not `##`, so a
  heading-level search misses them and the contract reads as if it has none.
  The parse check — every rule row becomes exactly one rule — is what catches
  the omission.
- **Integer cents are not enough on their own.** The answer file can hold
  nothing but integers while a float factor inside still rounds a true
  half-cent the wrong way. So every factor is an exact fraction of the
  contract's digits and `apply_factor` raises on a float.

## Assumptions

- **Invoices are read from the JSONL**: five ids are reused, and joining the
  CSVs on the id pools two invoices' lines. A reused id is reported with the
  later record's totals, as the labels do in all five cases.
- **"Business Day" is Monday to Friday**; no contract lists holidays.
- **hospital_2's "Service Day" (07:00–06:59) changes nothing**: invoices carry
  dates, not times. **hospital_3's amendment applies by service date** (A1.1.2).
- **No LLM in the pipeline**: rules first, a model only where they failed, and
  they did not. A parse that disagrees with its own text stops the run.
- **Categories use the labels' names** so results compare per category; the
  field is free text. hospital_5's `wrong_facility_or_tier_multiplier` is new.

## Conventions the text leaves open — settled by the labels

I tried both readings of each against the hospital_1 labels, then applied the
winner to every contract.

| question | decided | evidence |
|---|---|---|
| exclusion window of N days | inclusive of day N | 909 vs 905 exact totals |
| same service, patient, date billed twice | the repeat is not payable | 909 vs 905 |
| service date outside the term | flagged, amount kept | 909 vs 904 |
| service the contract does not list | flagged, amount kept | label totals |
| out of term and after the invoice | reported once, as out of term | label categories |

## Ambiguities I could not resolve

- **Daily-cap totals.** The labels pay fewer units than the cap on all four dev
  cases, and no clause explains the difference. The engine pays up to the cap,
  marks the amount disputed, and gives those rows 0.60, since the confidence
  covers the whole row, total included.
- **Do two premiums compound** (hospital_4 cl. 4.3), **and does the cap apply
  before the premium threshold?** I multiply in sequence and cap after. Neither
  case occurs in the data, so no number depends on it.

## The submission must not depend on the reports

`cli.py all` runs `submit` before `dev`, `validate` and `inject`, and a report
that fails is printed and skipped rather than ending the run (the exit code
still says so). The reports describe how well the audit did; the submission is
the audit. Scoring against the hospital_1 labels is the fragile step — on any
data those labels do not describe, it cannot run at all, and it should not take
`outputs/submission.csv` down with it. Run alone, every command still raises.
