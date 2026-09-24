"""
One entry point for everything the repo produces.

    PYTHONPATH=src python src/cli.py rules      # parse + check contracts, write rules/hospital_N.json
    PYTHONPATH=src python src/cli.py validate   # price agreement per service, hospitals 1-5
    PYTHONPATH=src python src/cli.py submit     # outputs/submission.csv, audit_detail.csv, review_queue.csv
    PYTHONPATH=src python src/cli.py dev        # score against the hospital_1 labels
    PYTHONPATH=src python src/cli.py inject     # planted-error recall on hospitals 2-5
    PYTHONPATH=src python src/cli.py all        # all of the above, in that order

Run from audit_repo/. Nothing here needs an API key.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RULES_DIR = Path("rules")


def rules() -> None:
    from contract_rules import CONTRACT_FILES, load_contract, rules_to_dict, summarise

    RULES_DIR.mkdir(exist_ok=True)
    for hospital_id in CONTRACT_FILES:
        parsed = load_contract(hospital_id)  # raises if the rules disagree with the text
        path = RULES_DIR / f"hospital_{hospital_id}.json"
        path.write_text(json.dumps(rules_to_dict(parsed), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{path}: {summarise(parsed)}")


def validate() -> None:
    import validate_rules
    validate_rules.main()


def dev() -> None:
    import evaluate
    evaluate.main()  # raises NoDevSet if the labels do not describe the invoices on disk


def submit() -> None:
    import build_submission
    submission = build_submission.write()
    print(f"outputs/submission.csv: {len(submission)} rows, {int(submission['flagged'].sum())} flagged")
    print("outputs/audit_detail.csv, outputs/review_queue.csv written")


def inject() -> None:
    import inject_errors
    inject_errors.main()


COMMANDS = {"rules": rules, "validate": validate, "submit": submit, "dev": dev, "inject": inject}

# `submit` runs before the reports on purpose. `dev`, `validate` and `inject`
# describe how well the audit did; `submit` *is* the audit's output. On a re-run
# against invoices I have never seen, the hospital_1 labels no longer describe
# the data and `dev` cannot score anything — that must not cost the submission.
# So `all` reports a failed step and carries on, and exits non-zero at the end;
# a single command still raises, because then the failure is the answer.
REPORTS = {"validate", "dev", "inject"}


def main(argv: list[str]) -> None:
    if len(argv) != 1 or argv[0] not in {*COMMANDS, "all"}:
        print(__doc__)
        sys.exit(2)
    everything = argv[0] == "all"
    failed: list[str] = []
    for name, command in COMMANDS.items():
        if not everything and argv[0] != name:
            continue
        print(f"\n### {name}\n")
        if not (everything and name in REPORTS):
            command()
            continue
        try:
            command()
        except Exception as exc:                      # noqa: BLE001 - reported, not swallowed
            failed.append(name)
            print(f"!! {name} could not run: {type(exc).__name__}: {exc}")
            print(f"!! continuing; this does not affect outputs/submission.csv")
    if failed:
        print(f"\n### {len(failed)} step(s) did not run: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
