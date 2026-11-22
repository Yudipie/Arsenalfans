"""Run every tests/cases/ file through the pipeline on a fresh database and compare verdicts.

  uv run python scripts/eval.py

A wrong verdict fails the run. A case whose result depends on a check that is not built yet is "pending".
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["CC_DB"] = os.path.join(tempfile.mkdtemp(), "eval.sqlite")

from cc import store, worker  # noqa: E402
from cc.checks import NOT_BUILT  # noqa: E402


def main() -> int:
    store.init()
    wrong = 0
    print(f"{'case':<5}{'expected':<15}{'got':<15}{'result':<9}{'s':>6}{'₹':>7}  notes")
    for path in sorted((ROOT / "tests" / "cases").glob("case_*.json")):
        case = json.loads(path.read_text())
        t0 = time.time()
        job_id = worker.submit(case["request"])
        worker.run_job(job_id)
        v = store.latest_verdict(job_id)
        got, out = v["verdict"], v["output"]
        facts = {e["fact_id"] for e in store.evidence(job_id)}
        missing = [f for f in case["expect_facts"] if f not in facts]
        pending = any(c["rule_id"] == NOT_BUILT for c in v["checks"])
        if got == case["expected"] and not missing:
            result = "ok"
        elif pending:
            result = "pending"
        else:
            result, wrong = "WRONG", wrong + 1
        notes = []
        if missing:
            notes.append(f"missing facts {missing}")
        if pending:
            notes.append("not built: " + ",".join(c["check"] for c in v["checks"] if c["rule_id"] == NOT_BUILT))
        print(f"{case['case']:<5}{case['expected']:<15}{got:<15}{result:<9}{time.time() - t0:>6.2f}"
              f"{out['cost_inr']:>7.2f}  {'; '.join(notes)}")
    print(f"\n{wrong} wrong verdict(s)")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
