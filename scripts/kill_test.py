"""20 random kills. Each round: fresh database, submit a job, start the worker, kill -9 it after a random delay,
restart it, wait for the verdict. Asserts the same verdict as an uninterrupted run, no stored call fetched
again (only a call in flight at the kill may repeat), and that approving twice leaves one approval row.

  uv run python scripts/kill_test.py        output also saved to logs/kill_test.txt
"""
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cc import output, store, worker  # noqa: E402
from cc.core import LOGS  # noqa: E402

ROUNDS = 20
STEP_DELAY = "0.12"  # simulated latency per step and per outside call, so kills land mid-job
CASES = [2, 4]       # the phase-1 cases with a GST call
lines: list[str] = []


def say(s: str) -> None:
    print(s)
    lines.append(s)


def fresh_db() -> None:
    os.environ["CC_DB"] = os.path.join(tempfile.mkdtemp(), "kill.sqlite")
    store.init()


def start_worker() -> subprocess.Popen:
    env = {**os.environ, "CC_STEP_DELAY": STEP_DELAY}
    return subprocess.Popen([sys.executable, "-m", "cc.worker"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_done(job_id: str, timeout: float = 30) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if store.job(job_id)["status"] == "awaiting_approval":
            return
        time.sleep(0.05)
    raise TimeoutError(job_id)


def request(n: int) -> dict:
    return json.loads((ROOT / "tests" / "cases" / f"case_{n:02d}.json").read_text())["request"]


def run(n: int, kill_after: float | None) -> dict:
    fresh_db()
    job_id = worker.submit(request(n))
    t0 = time.time()
    w = start_worker()
    killed_at = None
    if kill_after is not None:
        time.sleep(kill_after)
        if w.poll() is None:
            killed_at = store.job(job_id)["current_step"] or store.job(job_id)["status"]
            w.kill()
            w.wait()
            w = start_worker()
    try:
        wait_done(job_id)
    finally:
        w.kill()
        w.wait()
    calls = store.job_calls(job_id)
    v = store.latest_verdict(job_id)
    ids = [e["evidence_id"] for e in store.evidence(job_id)]
    h = output.approval_hash(v["verdict"], ids, store.job(job_id)["bank_hash"], v["rules_hash"])
    store.approve(job_id, h, "kill-test")
    store.approve(job_id, h, "kill-test")
    return {"verdict": v["verdict"], "elapsed": time.time() - t0, "killed_at": killed_at,
            "calls": len(calls), "repeats": sum(c["repeats"] for c in calls),
            "all_done": all(c["status"] == "done" for c in calls), "approvals": len(store.approvals(job_id)),
            "verdict_rows": store.connect().execute("SELECT COUNT(*) FROM verdicts WHERE job_id=?", (job_id,)).fetchone()[0]}


def main() -> int:
    random.seed(int(os.getenv("SEED", time.time())))
    base = {n: run(n, None) for n in CASES}
    for n, b in base.items():
        say(f"baseline case {n}: {b['verdict']} in {b['elapsed']:.2f}s, {b['calls']} call(s)")
    failures = 0
    for i in range(1, ROUNDS + 1):
        n = CASES[i % len(CASES)]
        delay = random.uniform(0, base[n]["elapsed"])
        r = run(n, delay)
        problems = []
        if r["verdict"] != base[n]["verdict"]:
            problems.append(f"verdict {r['verdict']} != {base[n]['verdict']}")
        if r["calls"] != base[n]["calls"] or not r["all_done"]:
            problems.append(f"calls {r['calls']} (all done: {r['all_done']})")
        if r["repeats"] > 1:
            problems.append(f"{r['repeats']} repeated calls (at most the 1 in flight)")
        if r["verdict_rows"] != 1:
            problems.append(f"{r['verdict_rows']} verdict rows")
        if r["approvals"] != 1:
            problems.append(f"{r['approvals']} approval rows after approving twice")
        failures += bool(problems)
        say(f"round {i:2}: case {n}, kill at {delay:.2f}s during {r['killed_at'] or 'nothing (job done)'}: "
            f"{r['verdict']}, {r['repeats']} repeated call(s) · " + ("; ".join(problems) or "ok"))
    say(f"\n{ROUNDS - failures}/{ROUNDS} passed")
    LOGS.mkdir(exist_ok=True)
    (LOGS / "kill_test.txt").write_text("\n".join(lines) + "\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
