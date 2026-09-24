# Decision log
 
## Assumptions
 
- **Invoices are read from the JSONL**: five ids are reused, and joining the
  CSVs on the id pools two invoices' lines. A reused id is reported with the
  later record's totals, as the labels do in all five cases.
- **hospital_2's "Service Day" (07:00–06:59) changes nothing**: invoices carry
  dates, not times, so each line's Service Date is its Service Day.
## Conventions the text leaves open — settled by the labels
 
I tried both readings of each against the hospital_1 labels, then applied the
winner to every contract.
 
| question | decided | evidence |
|---|---|---|
| exclusion window of N days | inclusive of day N | 909 vs 905 exact totals |
| service date outside the term | flagged, amount kept | 909 vs 904 |
| service the contract does not list | flagged, amount kept | label totals |
| out of term and after the invoice | reported once, as out of term | label categories |
 
## Ambiguities I could not resolve
 
- **Daily-cap totals.** The labels pay fewer units than the cap on all four dev
  cases, and no clause explains the difference. The engine pays up to the cap,
  marks the amount disputed, and gives those rows 0.60, since the confidence
  covers the whole row, total included.
- **Direction of the exclusion window on hospitals 3 and 5.** hospitals 1, 2
  and 4 state that the window runs in either direction from the service date;
  hospitals 3 and 5 say nothing. I applied the same reading to them. If their
  windows run backwards only, 9 excluded lines would become payable (3 on
  hospital_3, 6 on hospital_5).
- **Duplicate billing on hospital_2.** hospitals 1, 3, 4 and 5 state that "the
  same Service may not be billed twice for the same Patient and the same
  Service Date"; hospital_2 has no such clause. All 7 repeats there match the 4
  labelled duplicates on hospital_1, the same line copied onto a second
  invoice, so I applied the same rule. It flags 7 hospital_2 invoices, one of
  them on this finding alone.
- **Does a threshold premium compound with a non-business-day uplift?**
  hospital_4 cl. 4.1 puts "any premium or uplift" in one class, and cl. 4.3
  says adjustments are "not compounded within a single class". I apply both in
  sequence.
- **Is the premium threshold assessed before or after the daily cap?** I assess
  it on the billed quantity and cap after, as hospital_4 cl. 5.3 allows.
- No service in any contract carries both adjustments of either pair, so no
  number in the submission depends on these two readings.
