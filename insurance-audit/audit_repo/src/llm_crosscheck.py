"""
Optional: an LLM as an independent second reader of each contract.

The pipeline itself does not call a model — every contract parses with regex,
and src/validate_rules.py checks the result against billed prices. This script
is a second, independent check of the same extraction: a model reads the
contract text with prompts/extract_base_rates_v1.txt, and every service where
its base rate or unit basis disagrees with the regex parser is listed for a
human to settle against the text.

Neither reader is trusted over the other; a disagreement is a question, not an
answer. Output is cached in outputs/ (gitignored) so a re-run costs nothing.

    cp .env.example .env     # add GEMINI_API_KEY
    python src/llm_crosscheck.py 2
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from contract_rules import CONTRACT_FILES, DATA_DIR, load_contract

PROMPT_PATH = Path("prompts/extract_base_rates_v1.txt")
OUT_DIR = Path("outputs")


def extract_with_llm(hospital_id: int) -> dict:
    cache = OUT_DIR / f"llm_rates_hospital_{hospital_id}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    from dotenv import load_dotenv
    from google import genai

    load_dotenv()
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    contract = "\n\n".join((DATA_DIR / p).read_text(encoding="utf-8") for p in CONTRACT_FILES[hospital_id])
    prompt = PROMPT_PATH.read_text(encoding="utf-8").replace("{contract_text}", contract)
    response = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            # Extraction must be repeatable: the same contract has to produce
            # the same table on every run.
            "temperature": 0,
        },
    )
    result = json.loads(response.text)
    OUT_DIR.mkdir(exist_ok=True)
    cache.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def crosscheck(hospital_id: int) -> list[str]:
    rules = load_contract(hospital_id)
    llm = {s["name"]: s for s in extract_with_llm(hospital_id).get("services", [])}
    problems = []
    for name in sorted(set(rules.base_rates) | set(llm)):
        if name not in llm:
            problems.append(f"only the regex parser found: {name}")
        elif name not in rules.base_rates:
            problems.append(f"only the model found: {name}")
        elif llm[name]["rate_cents"] != rules.base_rates[name]:
            problems.append(f"rate differs: {name}: regex {rules.base_rates[name]}, model {llm[name]['rate_cents']}")
        elif llm[name].get("unit_basis") != rules.unit_basis[name]:
            problems.append(f"basis differs: {name}: regex {rules.unit_basis[name]!r}, model {llm[name]['unit_basis']!r}")
    return problems


if __name__ == "__main__":
    for hospital_id in [int(a) for a in sys.argv[1:]] or [2]:
        problems = crosscheck(hospital_id)
        print(f"hospital_{hospital_id}: {len(problems)} disagreement(s)")
        for line in problems:
            print("  " + line)
