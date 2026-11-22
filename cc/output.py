"""The only place output JSON is built: serialize(), the approver card and the approval hash.
Vendor card, Hindi and the card sentences (templates.yaml) arrive in phase 3."""
import hashlib
import time
from datetime import datetime, timezone


def _fact(e: dict) -> dict:
    return {k: e[k] for k in ("fact_id", "value", "source", "field_path", "retrieved_at", "call_id", "mock")}


def serialize(job: dict, record: dict, decision: dict, evidence: list[dict], calls: list[dict],
              rules_hash: str) -> dict:
    """Output JSON (overview §4.4). Each reason carries the facts it cites."""
    by_id = {e["fact_id"]: e for e in evidence}
    reasons = [{"check": r["check"], "rule_id": r["rule_id"], "text_en": None,
                "facts": [_fact(by_id[f]) for f in r["facts"] if f in by_id]} for r in decision["reasons"]]
    gaps = [{"check": g["check"], "rule_id": g["rule_id"], "missing": g["gap"], "sources_tried": g["sources_tried"]}
            for g in decision["gaps"]]
    now = time.time()
    return {
        "job_id": job["job_id"],
        "verdict": decision["verdict"],
        "reasons": reasons,
        "gaps": gaps,
        "flagged_lines": record.get("flagged_lines", []),
        "parse_notes": record.get("parse_notes", []),
        "cost_inr": round(sum(c["cost_inr"] * (1 + c["repeats"]) for c in calls), 2),
        "elapsed_s": round(now - (job["started_at"] or now), 1),
        "rules_hash": rules_hash,
        "decided_at": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
        "mock": any(e["mock"] for e in evidence),
    }


def approver_card(out: dict, record: dict, checks: list[dict]) -> dict:
    """Plain, structured card; no prose until templates.yaml (phase 3)."""
    v, b = record["vendor"], record["bank"]
    return {
        "verdict": out["verdict"],
        "carrier": v.get("name_as_written"),
        "gstin": v.get("gstin"),
        "pan_holder_type": v.get("pan_holder_type"),
        "bank": {"ifsc": b.get("ifsc"), "account_last4": b.get("account_last4"), "holder_checked": False},
        "checks": [{"check": c["check"], "result": c["result"], "rule_id": c["rule_id"]} for c in checks],
        "reasons": out["reasons"],
        "gaps": out["gaps"],
        "notes": out["parse_notes"],
        "mock": out["mock"],
    }


def approval_hash(verdict: str, evidence_ids: list[int], bank_hash: str | None, rules_hash: str) -> str:
    """sha256(verdict + evidence ids + bank hash + rules hash). Any change voids an approval."""
    parts = [verdict, ",".join(str(i) for i in sorted(evidence_ids)), bank_hash or "", rules_hash]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()
