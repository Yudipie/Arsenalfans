"""FastAPI app. Starts the worker as a subprocess so the page survives `kill -9` on it.
Run: `uv run uvicorn cc.app:app --reload`
"""
import json
import os
import signal
import subprocess
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from cc import output, store
from cc.core import ROOT, Request, mock, rules_hash
from cc.intake import scrub_request
from cc.worker import STEPS, redecide, submit

_worker: subprocess.Popen | None = None


def _start_worker() -> None:
    global _worker
    if _worker is None or _worker.poll() is not None:
        _worker = subprocess.Popen([sys.executable, "-m", "cc.worker"], cwd=ROOT)


@asynccontextmanager
async def lifespan(_app):
    store.init()
    _start_worker()
    yield
    if _worker and _worker.poll() is None:
        _worker.kill()


app = FastAPI(lifespan=lifespan)


class Approve(BaseModel):
    approval_hash: str
    approved_by: str = "demo-approver"


class Edit(BaseModel):
    field: str
    value: str | None


def _job_or_404(job_id: str) -> dict:
    job = store.job(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    return job


def _current_hash(job_id: str) -> str | None:
    """Recomputed from what is stored now, never taken from the page."""
    v, job = store.latest_verdict(job_id), store.job(job_id)
    # A verdict decided under other rules is stale: no hash, so nothing can be approved.
    if not v or job["status"] != "awaiting_approval" or v["rules_hash"] != rules_hash():
        return None
    ids = [e["evidence_id"] for e in store.evidence(job_id)]
    return output.approval_hash(v["verdict"], ids, job["bank_hash"], v["rules_hash"])


def _rules_stale(job_id: str) -> bool:
    """rules.yaml changed since this verdict: it can't be approved until it is decided again."""
    v, job = store.latest_verdict(job_id), store.job(job_id)
    return bool(v) and job["status"] == "awaiting_approval" and v["rules_hash"] != rules_hash()


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/samples")
def samples():
    """Sample requests for the page: the rehearsal case files."""
    out = []
    for p in sorted((ROOT / "tests" / "cases").glob("case_*.json")):
        c = json.loads(p.read_text())
        out.append({"label": f"{c['case']}. {c['title']}", "request": c["request"]})
    return out


@app.post("/jobs")
def post_job(request: dict):
    try:
        job_id = submit(request)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = _job_or_404(job_id)
    calls = store.job_calls(job_id)
    for c in calls:
        c["response"] = json.loads(c["response"]) if c["response"] else None
        c["request"] = json.loads(c["request"])
    v = store.latest_verdict(job_id)
    return {
        "job_id": job_id,
        "status": job["status"],
        "current_step": job["current_step"],
        "record": json.loads(job["record"]) if job["record"] else None,
        "steps": [{"name": s, "done": s in {r["name"] for r in store.steps(job_id)}} for s in STEPS],
        "resumes": json.loads(job["resumes"]),
        "evidence": store.evidence(job_id),
        "calls": calls,
        "verdict": v and {"checks": v["checks"], "output": v["output"], "card": v["card"]},
        "approval_hash": _current_hash(job_id),
        "rules_stale": _rules_stale(job_id),
        "approvals": store.approvals(job_id),
        "history": store.verdicts(job_id),
        "repeated_calls": sum(c["repeats"] for c in calls),
        "respent_inr": round(sum(c["repeats"] * c["cost_inr"] for c in calls), 2),
        "cost_inr": round(sum(c["cost_inr"] * (1 + c["repeats"]) for c in calls), 2),
        "mock": mock(),
    }


@app.post("/jobs/{job_id}/approve")
def approve(job_id: str, body: Approve):
    _job_or_404(job_id)
    if _rules_stale(job_id):
        raise HTTPException(409, f"rules changed since this verdict; re-run needed: POST /jobs/{job_id}/redecide")
    current = _current_hash(job_id)
    if current is None or body.approval_hash != current:
        raise HTTPException(409, "the verdict or its evidence changed; reload and review again")
    store.approve(job_id, current, body.approved_by)
    return {"approvals": store.approvals(job_id)}


@app.post("/jobs/{job_id}/edit")
def edit(job_id: str, body: Edit):
    job = _job_or_404(job_id)
    req = json.loads(job["request"])
    req["fields"][body.field] = body.value
    try:
        clean = scrub_request(Request(**req).model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(422, str(e))
    if not store.edit(job_id, clean):
        raise HTTPException(409, "the job is not finished yet (queued, running or being redecided); edit it once the verdict is out")
    return {"job_id": job_id}


@app.post("/jobs/{job_id}/redecide")
def redecide_job(job_id: str):
    """Recheck stored evidence with the current rules.yaml; no outside calls."""
    job = _job_or_404(job_id)
    if job["status"] != "awaiting_approval":
        raise HTTPException(409, f"job is {job['status']}; redecide after it finishes")
    out = redecide(job_id)
    if out is None:
        raise HTTPException(409, "job changed or a redecide is already running; try again")
    return out


@app.get("/worker")
def worker_status():
    alive = _worker is not None and _worker.poll() is None
    return {"alive": alive, "pid": _worker.pid if alive else None, "mock": mock()}


@app.post("/worker/kill")
def worker_kill():
    if _worker and _worker.poll() is None:
        os.kill(_worker.pid, signal.SIGKILL)
        _worker.wait()
    return worker_status()


@app.post("/worker/restart")
def worker_restart():
    _start_worker()
    return worker_status()
