"""Worker: a separate process that runs each job's steps in order, checkpointing each one.

On start it resumes any unfinished job from its first unfinished step; stored calls are reused.
Run: `uv run python -m cc.worker` (cc/app.py starts it for you).
"""
import hashlib
import json
import os
import time

from cc import checks, output, sources, store
from cc.core import LOGS, CheckResult, OnboardingRecord, Request, load_rules, rules_hash
from cc.intake import check_formats, record_from_request, scrub_request

STEPS = ["intake", "formats", "resolve", "gather", "checks", "decide", "output"]


def _log(**kw) -> None:
    """JSON lines; ids and step names only, so nothing personal lands here."""
    LOGS.mkdir(exist_ok=True)
    with (LOGS / "worker.jsonl").open("a") as f:
        f.write(json.dumps({"t": round(time.time(), 3), "pid": os.getpid(), **kw}) + "\n")


# Each step takes (job, done) and returns (output, extra), where extra(conn) writes inside the step's transaction.

def step_intake(job, done):
    req = Request(**json.loads(job["request"]))
    rec = record_from_request(job["job_id"], req)

    def extra(c):
        c.execute("UPDATE jobs SET record=?, gstin=?, pan=?, bank_hash=? WHERE job_id=?",
                  (rec.model_dump_json(), rec.vendor.gstin, rec.vendor.pan, rec.bank.account_hash, job["job_id"]))
    return rec.model_dump(), extra


def step_formats(job, done):
    c1, notes = check_formats(OnboardingRecord(**done["intake"]))
    return {"c1": c1.model_dump(), "parse_notes": notes}, None


def step_resolve(job, done):
    if done["formats"]["c1"]["result"] != "pass":
        return {"gstins": [], "note": "c1 did not pass: no paid call"}, None
    return {"gstins": [done["intake"]["vendor"]["gstin"]]}, None


def step_gather(job, done):
    gstins = done["resolve"]["gstins"]
    if not gstins:
        return {"skipped": done["resolve"].get("note", "no GSTIN to look up"), "dropped": 0}, None
    facts, tried, unavailable = [], [], False
    for g in gstins:
        try:
            facts += sources.gst_lookup(job["job_id"], g)
        except sources.OverLimit as e:  # cost cap or time limit, checked inside call() before each paid call
            return {"skipped": e.rule_id.replace("_", " "), "limit_rule": e.rule_id, "dropped": 0}, None
        except sources.SourceUnavailable as e:
            tried, unavailable = e.sources_tried, True
    responses = {f["call_id"]: json.loads(store.call_by_id(f["call_id"])["response"]) for f in facts}
    kept, dropped = checks.validate(facts, responses)
    tried = tried or sorted({f["source"] for f in kept})
    return ({"sources_tried": tried, "unavailable": unavailable, "kept": len(kept), "dropped": len(dropped)},
            lambda c: store.add_evidence(c, job["job_id"], kept))


def step_checks(job, done):
    results = [
        CheckResult(**done["formats"]["c1"]),
        checks.c2(store.evidence(job["job_id"]), done["gather"]),
        checks.not_built("c3", 2),
        checks.not_built("c4", 2),
        checks.not_built("c9", 2),
    ]
    return {"results": [r.model_dump() for r in results]}, None


def step_decide(job, done):
    results = [CheckResult(**r) for r in done["checks"]["results"]]
    return checks.decide(results, load_rules()), None


def step_output(job, done):
    job_id = job["job_id"]
    job = store.job(job_id)
    record = {**done["intake"], "parse_notes": done["intake"]["parse_notes"] + done["formats"]["parse_notes"]}
    ev, calls, rh = store.evidence(job_id), store.job_calls(job_id), rules_hash()
    out = output.serialize(job, record, done["decide"], ev, calls, rh)
    card = output.cards(out, record, done["checks"]["results"])
    card["approver"]["approval_hash"] = output.approval_hash(out["verdict"], [e["evidence_id"] for e in ev],
                                                             job["bank_hash"], rh)

    def extra(c):
        c.execute("INSERT INTO verdicts (job_id, verdict, checks, output, card, rules_hash, decided_at) VALUES (?,?,?,?,?,?,?)",
                  (job_id, out["verdict"], json.dumps(done["checks"]["results"]), json.dumps(out, default=str),
                   json.dumps(card, default=str), rh, time.time()))
        c.execute("UPDATE jobs SET status='awaiting_approval', current_step=NULL, finished_at=? WHERE job_id=?",
                  (time.time(), job_id))
    return {"verdict": out["verdict"]}, extra


STEP_FNS = {"intake": step_intake, "formats": step_formats, "resolve": step_resolve, "gather": step_gather,
            "checks": step_checks, "decide": step_decide, "output": step_output}


def submit(request: dict) -> str:
    """Validate, scrub (no raw account number or Aadhaar is ever stored), then queue. Returns the job_id."""
    clean = scrub_request(Request(**request).model_dump(exclude_none=True))
    job_id = hashlib.sha256((clean["client_id"] + json.dumps(clean, sort_keys=True)).encode()).hexdigest()[:16]
    store.submit(job_id, clean["client_id"], clean)
    return job_id


def run_job(job_id: str) -> None:
    job = store.job(job_id)
    done = store.done_steps(job_id)
    first = next((s for s in STEPS if s not in done), None)
    # With one worker, a job already 'running' at pickup was interrupted by a kill.
    store.start_job(job_id, first if job["status"] == "running" else None)
    if job["status"] == "running":
        _log(event="resume", job_id=job_id, step=first)
    delay = float(os.getenv("CC_STEP_DELAY", "0"))
    for name in STEPS:
        if name in done:
            continue
        store.set_current(job_id, name)
        started = time.time()
        out, extra = STEP_FNS[name](job, done)
        time.sleep(delay)  # kill-test hook: widens the window between work and commit
        store.finish_step(job_id, name, out, started, extra)
        done[name] = out
        _log(event="step", job_id=job_id, step=name)


REDECIDE_STEPS = ["checks", "decide", "output"]


def redecide(job_id: str) -> dict | None:
    """Rerun checks, decide and output from stored evidence with the current rules.yaml and templates.yaml.
    No outside calls (sources.OFFLINE). Adds a verdicts row; voids approvals made under a different hash.
    Returns None if the job is not awaiting approval (still running, edited, or another redecide is on).
    The output step hands the job back to awaiting_approval; a failure before it hands it back unchanged."""
    if not store.claim_redecide(job_id):
        return None
    job = store.job(job_id)
    done = store.done_steps(job_id)
    token = sources.OFFLINE.set(True)
    try:
        for name in REDECIDE_STEPS:
            started = time.time()
            out, extra = STEP_FNS[name](job, done)
            store.finish_step(job_id, name, out, started, extra)
            done[name] = out
    except BaseException:
        store.release_redecide(job_id)
        raise
    finally:
        sources.OFFLINE.reset(token)
    v, job = store.latest_verdict(job_id), store.job(job_id)
    new_hash = output.approval_hash(v["verdict"], [e["evidence_id"] for e in store.evidence(job_id)],
                                    job["bank_hash"], v["rules_hash"])
    store.void_approvals_except(job_id, new_hash)
    _log(event="redecide", job_id=job_id, verdict=v["verdict"])
    return {"verdict": v["verdict"], "approval_hash": new_hash}


def main() -> None:
    store.init()
    _log(event="start")
    while True:
        job = store.next_job()
        if job:
            run_job(job["job_id"])
        else:
            time.sleep(0.2)


if __name__ == "__main__":
    main()
