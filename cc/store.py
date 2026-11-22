"""SQLite store: WAL mode, the 6 tables (overview §4.5), one transaction per step.

The database path is CC_DB (default logs/cc.sqlite); eval and the kill test point it at a fresh file.
"""
import json
import os
import sqlite3
import time
from contextlib import contextmanager

from cc.core import LOGS

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY, client_id TEXT, request TEXT, record TEXT, status TEXT, current_step TEXT,
  gstin TEXT, pan TEXT, bank_hash TEXT, created_at REAL, started_at REAL, finished_at REAL,
  resumes TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS steps (
  job_id TEXT, name TEXT, status TEXT, output TEXT, started_at REAL, finished_at REAL,
  PRIMARY KEY (job_id, name));
CREATE TABLE IF NOT EXISTS calls (
  call_id INTEGER PRIMARY KEY, call_key TEXT UNIQUE, provider TEXT, request TEXT, response TEXT,
  status TEXT, repeats INTEGER DEFAULT 0, cost_inr REAL DEFAULT 0, tokens_in INTEGER, tokens_out INTEGER,
  request_id TEXT, job_id TEXT, mock INTEGER DEFAULT 0, started_at REAL, finished_at REAL);
CREATE TABLE IF NOT EXISTS evidence (
  evidence_id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, fact_id TEXT, value TEXT, source TEXT,
  field_path TEXT, quote TEXT, retrieved_at TEXT, record_updated_at TEXT, call_id INTEGER, mock INTEGER);
CREATE TABLE IF NOT EXISTS verdicts (
  verdict_id INTEGER PRIMARY KEY, job_id TEXT, verdict TEXT, checks TEXT, output TEXT, card TEXT,
  rules_hash TEXT, decided_at REAL);
CREATE TABLE IF NOT EXISTS approvals (
  approval_id INTEGER PRIMARY KEY, job_id TEXT, approval_hash TEXT, approved_by TEXT, approved_at REAL,
  void INTEGER DEFAULT 0, UNIQUE (job_id, approval_hash));
"""


def db_path() -> str:
    return os.getenv("CC_DB") or str(LOGS / "cc.sqlite")


def connect() -> sqlite3.Connection:
    LOGS.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path(), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def tx():
    """One transaction: all of it lands or none of it does (E44)."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    conn.close()


def _one(sql: str, *args) -> dict | None:
    conn = connect()
    row = conn.execute(sql, args).fetchone()
    conn.close()
    return dict(row) if row else None


def _all(sql: str, *args) -> list[dict]:
    conn = connect()
    rows = [dict(r) for r in conn.execute(sql, args)]
    conn.close()
    return rows


# --- jobs and steps ---

def submit(job_id: str, client_id: str, request: dict) -> None:
    """Idempotent: the same request gives the same job_id and the existing job."""
    with tx() as c:
        c.execute("INSERT OR IGNORE INTO jobs (job_id, client_id, request, status, created_at) VALUES (?,?,?,?,?)",
                  (job_id, client_id, json.dumps(request), "queued", time.time()))


def job(job_id: str) -> dict | None:
    return _one("SELECT * FROM jobs WHERE job_id=?", job_id)


def next_job() -> dict | None:
    return _one("SELECT * FROM jobs WHERE status IN ('queued','running') ORDER BY created_at LIMIT 1")


def done_steps(job_id: str) -> dict:
    return {r["name"]: json.loads(r["output"]) for r in
            _all("SELECT name, output FROM steps WHERE job_id=? AND status='done'", job_id)}


def steps(job_id: str) -> list[dict]:
    return _all("SELECT name, status, started_at, finished_at FROM steps WHERE job_id=? ORDER BY finished_at", job_id)


def start_job(job_id: str, resumed_at: str | None) -> None:
    with tx() as c:
        if resumed_at:
            resumes = json.loads(c.execute("SELECT resumes FROM jobs WHERE job_id=?", (job_id,)).fetchone()[0])
            resumes.append({"step": resumed_at, "at": time.time()})
            c.execute("UPDATE jobs SET resumes=? WHERE job_id=?", (json.dumps(resumes), job_id))
        c.execute("UPDATE jobs SET status='running', started_at=COALESCE(started_at, ?) WHERE job_id=?",
                  (time.time(), job_id))


def set_current(job_id: str, step: str) -> None:
    with tx() as c:
        c.execute("UPDATE jobs SET current_step=? WHERE job_id=?", (step, job_id))


def finish_step(job_id: str, name: str, output: dict, started_at: float, extra=None) -> None:
    """The step row and its side writes (evidence, verdict, job fields) in one transaction."""
    with tx() as c:
        if extra:
            extra(c)
        c.execute("INSERT OR REPLACE INTO steps VALUES (?,?,?,?,?,?)",
                  (job_id, name, "done", json.dumps(output, default=str), started_at, time.time()))


def edit(job_id: str, request: dict) -> bool:
    """New details: drop the steps and evidence, void approvals, queue again. Stored calls stay and are reused.
    Refused (False) while a redecide is writing the job's steps."""
    with tx() as c:
        if c.execute("UPDATE jobs SET request=?, status='queued', current_step=NULL, finished_at=NULL "
                     "WHERE job_id=? AND status<>'redeciding'", (json.dumps(request), job_id)).rowcount == 0:
            return False
        c.execute("DELETE FROM steps WHERE job_id=?", (job_id,))
        c.execute("DELETE FROM evidence WHERE job_id=?", (job_id,))
        c.execute("UPDATE approvals SET void=1 WHERE job_id=?", (job_id,))
    return True


def claim_redecide(job_id: str) -> bool:
    """Only a finished job is redecided, by one request at a time. The worker never picks up a 'redeciding'
    job and an edit is refused until it is handed back."""
    with tx() as c:
        return c.execute("UPDATE jobs SET status='redeciding' WHERE job_id=? AND status='awaiting_approval'",
                         (job_id,)).rowcount == 1


def release_redecide(job_id: str) -> None:
    with tx() as c:
        c.execute("UPDATE jobs SET status='awaiting_approval' WHERE job_id=? AND status='redeciding'", (job_id,))


# --- calls ---

def call_row(call_key: str) -> dict | None:
    return _one("SELECT * FROM calls WHERE call_key=?", call_key)


def call_by_id(call_id: int) -> dict | None:
    return _one("SELECT * FROM calls WHERE call_id=?", call_id)


def call_started(call_key: str, provider: str, request: dict, job_id: str) -> None:
    """Written before the request goes out. Finding it still in flight later means a kill: counted as a repeat."""
    with tx() as c:
        row = c.execute("SELECT status FROM calls WHERE call_key=?", (call_key,)).fetchone()
        if row is None:
            c.execute("INSERT INTO calls (call_key, provider, request, status, job_id, started_at) VALUES (?,?,?,?,?,?)",
                      (call_key, provider, json.dumps(request), "in_flight", job_id, time.time()))
        elif row["status"] == "in_flight":
            c.execute("UPDATE calls SET repeats=repeats+1, started_at=? WHERE call_key=?", (time.time(), call_key))
        else:
            c.execute("UPDATE calls SET status='in_flight', started_at=? WHERE call_key=?", (time.time(), call_key))


def call_finished(call_key: str, response, cost_inr: float, mock: bool, status: str = "done",
                  tokens: tuple = (None, None)) -> None:
    with tx() as c:
        c.execute("""UPDATE calls SET response=?, status=?, cost_inr=?, mock=?, finished_at=?, tokens_in=?,
                     tokens_out=? WHERE call_key=?""",
                  (json.dumps(response, default=str), status, cost_inr, int(mock), time.time(), *tokens, call_key))


def job_calls(job_id: str) -> list[dict]:
    """Calls this job used: ones it made, plus stored ones its evidence points at."""
    return _all("""SELECT * FROM calls WHERE job_id=? OR call_id IN (SELECT call_id FROM evidence WHERE job_id=?)
                   ORDER BY call_id""", job_id, job_id)


# --- evidence, verdicts, approvals ---

def evidence(job_id: str) -> list[dict]:
    rows = _all("SELECT * FROM evidence WHERE job_id=? ORDER BY evidence_id", job_id)
    for r in rows:
        r["value"], r["mock"] = json.loads(r["value"]), bool(r["mock"])
    return rows


def add_evidence(c: sqlite3.Connection, job_id: str, facts: list[dict]) -> None:
    for f in facts:
        c.execute("""INSERT INTO evidence (job_id, fact_id, value, source, field_path, quote, retrieved_at,
                     record_updated_at, call_id, mock) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                  (job_id, f["fact_id"], json.dumps(f["value"]), f["source"], f["field_path"], f["quote"],
                   f["retrieved_at"], f["record_updated_at"], f["call_id"], int(f["mock"])))


def latest_verdict(job_id: str) -> dict | None:
    v = _one("SELECT * FROM verdicts WHERE job_id=? ORDER BY verdict_id DESC LIMIT 1", job_id)
    if v:
        for k in ("checks", "output", "card"):
            v[k] = json.loads(v[k])
    return v


def approve(job_id: str, approval_hash: str, approved_by: str) -> None:
    """Approving twice under the same hash leaves one row. A row voided by an earlier redecide is made live
    again (rules A -> B -> A brings back hash A)."""
    with tx() as c:
        c.execute("INSERT INTO approvals (job_id, approval_hash, approved_by, approved_at) VALUES (?,?,?,?) "
                  "ON CONFLICT (job_id, approval_hash) DO UPDATE SET void=0, approved_by=excluded.approved_by, "
                  "approved_at=excluded.approved_at WHERE void=1",
                  (job_id, approval_hash, approved_by, time.time()))


def verdicts(job_id: str) -> list[dict]:
    """Every verdict for the job, oldest first; redecide adds rows so old and new can be compared."""
    rows = _all("SELECT verdict_id, verdict, output, rules_hash, decided_at FROM verdicts WHERE job_id=? "
                "ORDER BY verdict_id", job_id)
    for r in rows:
        out = json.loads(r.pop("output"))
        r["reasons"] = [x["rule_id"] for x in out["reasons"]]
        r["cost_inr"] = out["cost_inr"]
    return rows


def void_approvals_except(job_id: str, approval_hash: str) -> None:
    with tx() as c:
        c.execute("UPDATE approvals SET void=1 WHERE job_id=? AND approval_hash<>?", (job_id, approval_hash))


def approvals(job_id: str) -> list[dict]:
    return _all("SELECT * FROM approvals WHERE job_id=? ORDER BY approval_id", job_id)
