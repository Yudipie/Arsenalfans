"""Worker: a separate process that runs each job's steps in order, checkpointing each one.

On start it resumes any unfinished job from its first unfinished step; stored calls are reused.
Run: `uv run python -m cc.worker` (cc/app.py starts it for you).
"""
import hashlib
import json
import os
import sqlite3
import time
from urllib.parse import urlparse

from cc import checks, output, resolve, sources, store
from cc.core import LOGS, CheckResult, OnboardingRecord, Request, load_rules, rules_hash
from cc.intake import check_formats, pan_from_gstin, record_from_request, scrub_request

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

    pan = rec.vendor.pan or (pan_from_gstin(rec.vendor.gstin) if rec.vendor.gstin else None)

    def extra(c):
        c.execute("UPDATE jobs SET record=?, gstin=?, pan=?, bank_hash=? WHERE job_id=?",
                  (rec.model_dump_json(), rec.vendor.gstin, pan, rec.bank.account_hash, job["job_id"]))
    return rec.model_dump(), extra


def step_formats(job, done):
    c1, notes = check_formats(OnboardingRecord(**done["intake"]))
    return {"c1": c1.model_dump(), "parse_notes": notes}, None


def _over_limits(job: dict, rules: dict) -> str | None:
    """For resolve's multi-call stages: the limits call() enforces, as already reached by what is spent."""
    hit = sources.limit_hit(job["job_id"], "", rules)
    return hit.replace("_", " ") if hit else None


def _validated(facts: list[dict]) -> tuple[list[dict], list[dict]]:
    responses = {f["call_id"]: json.loads(store.call_by_id(f["call_id"])["response"]) for f in facts}
    return checks.validate(facts, responses)


def step_resolve(job, done):
    """Given GSTIN: hand it on (plus Crustdata identity). No GSTIN: discovery, name -> GSTINs -> one PAN."""
    rules = load_rules()
    out = resolve.resolve(job["job_id"], done["intake"], done["formats"]["c1"], rules,
                          lambda: _over_limits(store.job(job["job_id"]), rules))
    kept, dropped = _validated(out.pop("facts"))
    out.update(kept=len(kept), dropped=len(dropped))
    return out, (lambda c: store.add_evidence(c, job["job_id"], kept)) if kept else None


def step_gather(job, done):
    gstins = done["resolve"]["gstins"]
    if not gstins:
        return {"skipped": done["resolve"].get("note") or "no single GSTIN to look up", "dropped": 0}, None
    job_id, bank = job["job_id"], done["intake"]["bank"]
    facts, tried, unavailable = [], [], False
    for g in gstins:
        try:
            facts += sources.gst_lookup(job_id, g)
        except sources.OverLimit as e:  # cost cap or time limit, checked inside call() before each paid call
            return {"skipped": e.rule_id.replace("_", " "), "limit_rule": e.rule_id, "dropped": 0}, None
        except sources.SourceUnavailable as e:
            tried, unavailable = e.sources_tried, True
    gst_tried = tried or sorted({f["source"] for f in facts})
    ifsc_tried, ifsc_unavailable = load_rules()["providers"]["ifsc"], False
    if bank.get("ifsc"):
        try:
            facts += sources.ifsc_lookup(job_id, bank["ifsc"])
        except sources.SourceUnavailable as e:
            ifsc_tried, ifsc_unavailable = e.sources_tried, True
    gstin = gstins[0]
    facts += sources.vendor_master_facts(job_id, gstin, pan_from_gstin(gstin))
    facts += sources.earlier_job_facts(job_id, gstin, pan_from_gstin(gstin))
    # c5/c6: the website Crustdata lists, live or not, and its domain age. Free, best effort.
    site_tried, website = [], done["resolve"].get("website")
    if website:
        domain = urlparse(website if "//" in website else f"http://{website}").hostname or ""
        for fn, arg in ((sources.site_facts, website), (sources.rdap_facts, domain.removeprefix("www."))):
            try:
                facts += fn(job_id, arg)
                site_tried.append(fn.__name__.removesuffix("_facts"))
            except sources.SourceUnavailable:
                pass
    # c7: directors' names come from the stored request only; the record and this output never hold them.
    rules = load_rules()
    directors = (json.loads(job["request"]).get("fields") or {}).get("directors") or []
    day = int(store.job(job_id)["created_at"] // 86400 * 86400)
    people = resolve.screen_directors(job_id, directors, done["intake"], rules, day,
                                      lambda: _over_limits(store.job(job_id), rules))
    facts += people.pop("facts")
    purge = people.pop("purge")
    kept, dropped = _validated(facts)

    def extra(c):
        store.add_evidence(c, job_id, kept)
        store.purge_calls(c, purge)
    return ({"sources_tried": gst_tried, "unavailable": unavailable, "ifsc_tried": ifsc_tried,
             "ifsc_unavailable": ifsc_unavailable, "site_tried": site_tried, "people": people,
             "kept": len(kept), "dropped": len(dropped)}, extra)


def step_checks(job, done):
    rules, ev, rec = load_rules(), store.evidence(job["job_id"]), done["intake"]
    c1 = CheckResult(**done["formats"]["c1"])
    gstins = done["resolve"]["gstins"]
    if c1.rule_id == "gstin_missing" and gstins:  # discovery found the GSTIN: check its format now
        found = OnboardingRecord(**{**rec, "vendor": {**rec["vendor"], "gstin": gstins[0]}})
        c1, _ = check_formats(found)
    gstin = gstins[0] if gstins else None
    results = [
        c1,
        checks.c2(ev, done["gather"]),
        checks.c3(ev, done["resolve"], rec, rules, done["gather"]),
        checks.c4(ev, done["gather"], rec, gstin, rules),
        checks.c9(ev, done["gather"], rec, gstin, rules),
        checks.c5(ev, done["gather"], rules),
        checks.c6(ev, done["gather"], rec, rules),
        checks.c7(done["gather"], rules),
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
    raw = Request(**request).model_dump(exclude_none=True)
    clean = scrub_request(raw)
    # Hash the pre-scrub request: requests that differ only in masked numbers must not share a job. Only the hash is kept.
    job_id = hashlib.sha256((clean["client_id"] + json.dumps(raw, sort_keys=True)).encode()).hexdigest()[:16]
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
            try:
                run_job(job["job_id"])
            except Exception as e:
                # Transient (database is locked or busy): crash, restart, resume. Anything else parks the job.
                if isinstance(e, sqlite3.OperationalError) and ("locked" in str(e) or "busy" in str(e)):
                    raise
                store.fail_job(job["job_id"])
                _log(event="error", job_id=job["job_id"], error=type(e).__name__)
        else:
            time.sleep(0.2)


if __name__ == "__main__":
    main()
