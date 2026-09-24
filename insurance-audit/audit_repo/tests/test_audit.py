"""
Regression tests for the failures found while building this pipeline.

Each test pins one behaviour that was once wrong, so it cannot come back
silently. Run from audit_repo/:

    PYTHONPATH=src python -m pytest -q
"""

import tempfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from audit import audit_hospital, one_row_per_invoice_id
from contract_rules import load_contract
from loader import load_hospital
from matcher import ServiceMatcher, contained, tokenize
from pricing import apply_factor


def test_half_up_rounding_is_exact():
    # 20625 * 0.7 is 14437.4999... in binary floating point; the contract's
    # half-up rule on the true value 14437.5 gives 14438.
    from fractions import Fraction
    assert apply_factor(20625, Fraction(7, 10)) == 14438
    assert apply_factor(25, Fraction(1, 2)) == 13  # a plain half rounds away from zero


def test_money_never_touches_a_float():
    # The task: every monetary value is integer cents, and no float may appear.
    import pytest
    from audit import audit, review_queue
    with pytest.raises(TypeError):
        apply_factor(20625, 0.7)
    verdicts, priced, _ = audit(2)
    for frame in (verdicts, priced, review_queue(verdicts, priced)):
        for column in frame.columns:
            if "cents" in column:
                assert frame[column].dtype.kind == "i", column


def test_partial_description_is_ambiguous_on_text_alone():
    # Drops the qualifier: fits several Immunologic Endoscopic Procedures.
    rules = load_contract(1)
    candidates = ServiceMatcher(rules).candidates("Procedure Immun Endosc")
    assert len(candidates) > 1
    assert "Preoperative Immunologic Endoscopic Procedure" in candidates


def test_price_corroboration_breaks_the_tie():
    rules = load_contract(1)
    match = ServiceMatcher(rules).match("Procedure Immun Endosc", "per_procedure", 151975)
    assert match.service == "Preoperative Immunologic Endoscopic Procedure"
    assert match.how == "corroborated"


def test_each_token_must_cover_a_distinct_word():
    assert contained(tokenize("Immun Endosc Proc"), "preoperative immunologic endoscopic procedure".split())
    assert not contained(tokenize("Immun Immun"), "preoperative immunologic endoscopic procedure".split())


def test_amendment_reprices_by_service_date_not_invoice_date():
    rules = load_contract(3)
    service = "Assisted Urologic Endoscopic Procedure"
    assert rules.rate_on(service, date(2024, 12, 31)) == 94250
    assert rules.rate_on(service, date(2025, 1, 1)) == 111225


def test_multiplier_tables_are_read_from_the_markdown():
    # hospital_5's Tables 2 and 3 sit under ### headings, easy to miss
    # from the text sources; they sit under ### headings in the .md.
    rules = load_contract(5)
    assert len(rules.facility_factors) == len(rules.base_rates)
    assert len(rules.tier_factors) == len(rules.base_rates)


def test_reused_invoice_ids_keep_their_own_lines():
    invoices, lines = load_hospital(1)
    records = invoices.loc[invoices["invoice_id"] == "INV-H1-000548", "record"]
    assert len(records) == 2
    patients = lines[lines["record"].isin(records)].groupby("record")["patient_id"].first()
    assert patients.nunique() == 2


def test_dev_set_end_to_end():
    labels = pd.read_csv("data/labels/hospital_1_labels.csv")
    result = one_row_per_invoice_id(audit_hospital(1)).merge(labels, on="invoice_id")
    assert (result["flagged"] == result["is_erroneous"]).all()
    assert (result["expected_total_cents_x"] == result["expected_total_cents_y"]).sum() >= 909


def test_unrecognised_billing_slug_is_a_basis_mismatch():
    # "per_hour_per_item" never occurs in hospital_1. It was once missing from
    # the slug map, and an unmapped slug silently switched the check off.
    from audit import audit_lines
    _, priced, _ = audit_lines(2)
    line = priced[(priced["invoice_id"] == "INV-H2-000290") & (priced["unit_basis_as_billed"] == "per_hour_per_item")]
    assert line["basis_mismatch"].all()


# --- exact factors and parse-time checks ------------------------------------

def test_factors_are_exact_fractions_from_the_text():
    from fractions import Fraction
    rules = load_contract(5)
    factors = [f for table in rules.facility_factors.values() for f in table.values()]
    assert all(isinstance(f, Fraction) for f in factors)
    assert all(isinstance(f, Fraction) for f in load_contract(2).weekend_uplifts.values())


def _contracts_copy(tmp_path):
    import shutil
    shutil.copytree("data/contracts", tmp_path / "contracts")
    return tmp_path


def test_a_dropped_prose_rule_fails_the_parse(tmp_path):
    # Reword one discount sentence so the rule regex misses it: the template
    # count still sees the sentence, so the parse must refuse, not drop it.
    import pytest
    from contract_rules import ContractParseError
    data = _contracts_copy(tmp_path)
    path = data / "contracts/hospital_2/master_services_agreement.md"
    path.write_text(path.read_text().replace(" discount of ", " rebate of ", 1))
    with pytest.raises(ContractParseError, match="volume discount tiers"):
        load_contract(2, data_dir=data)


def test_a_rate_not_in_the_text_fails_the_parse():
    from contract_rules import validate_contract
    rules = load_contract(1)
    text = (Path("data") / "contracts/hospital_1/provider_services_agreement.md").read_text()
    service = next(iter(rules.base_rates))
    rules.base_rates[service] += 1
    assert any("not in the contract text" in p for p in validate_contract(rules, [text], prose=False))


def test_a_dropped_table_row_fails_the_parse():
    from contract_rules import validate_contract
    rules = load_contract(4)
    text = (Path("data") / "contracts/hospital_4/conditional_reimbursement_agreement.md").read_text()
    rules.exclusions.popitem()
    assert any("exclusion windows" in p for p in validate_contract(rules, [text], prose=False))


def test_rules_export_is_plain_json():
    import json
    from contract_rules import rules_to_dict
    exported = json.loads(json.dumps(rules_to_dict(load_contract(3))))
    periods = exported["services"]["Assisted Urologic Endoscopic Procedure"]["rate_periods"]
    assert [p["rate"]["cents"] for p in periods] == [94250, 111225]


# --- evidence a reviewer can check -------------------------------------------

def test_capped_line_shows_its_calculation_and_a_disputed_amount():
    from audit import audit
    verdicts, priced, _ = audit(1)
    line = priced[priced["line_id"] == "H1-L00015-11"].iloc[0]
    assert "daily cap 4" in line["calculation"]
    row = verdicts[verdicts["invoice_id"] == "INV-H1-000015"].iloc[0]
    assert row["amount_status"] == "disputed" and row["confidence"] == 0.60


def test_review_queue_covers_every_flagged_invoice():
    from audit import audit, review_queue
    verdicts, priced, _ = audit(1)
    queue = review_queue(verdicts, priced)
    assert set(queue["record"]) == set(verdicts.loc[verdicts["flagged"].eq(1), "record"])


def test_out_of_term_line_keeps_its_billed_amount():
    # Found by the injection harness: an out-of-term line was re-priced at the
    # contract rate, contradicting the documented convention.
    from audit import audit
    from datetime import timedelta
    invoices, lines = load_hospital(3)
    row = lines.index[0]
    lines.at[row, "service_date"] = pd.Timestamp(load_contract(3).term[1]) + timedelta(days=42)
    priced = audit(3, (invoices, lines))[1]
    line = priced[priced["line_id"] == lines.at[row, "line_id"]].iloc[0]
    assert line["out_of_term"] and line["expected_line_cents"] == line["line_total_cents"]


def test_planted_errors_are_found_on_an_unlabelled_hospital():
    from inject_errors import run_hospital
    table = run_hospital(2, per_type=3)
    assert (table["detected"] == table["n"]).all() and (table["named"] == table["n"]).all()
    assert table["collateral"].sum() == 0


def test_the_submission_survives_invoices_the_labels_do_not_describe():
    # The dev report scores against a fixed label file, so it cannot run at all
    # on invoices those labels do not describe. A scorer that dies must not take
    # the submission with it: `cli.py all` used to stop at the dev report and
    # leave outputs/ empty.
    import evaluate
    import pandas as pd
    labels = pd.read_csv(evaluate.LABELS_PATH)
    unseen = labels.assign(invoice_id=labels["invoice_id"] + "-UNSEEN")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "labels.csv"
        unseen.to_csv(path, index=False)
        original, evaluate.LABELS_PATH = evaluate.LABELS_PATH, path
        try:
            with pytest.raises(evaluate.NoDevSet):
                evaluate.main()
        finally:
            evaluate.LABELS_PATH = original
