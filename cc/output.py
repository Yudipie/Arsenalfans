"""The only place output JSON is built: serialize(), the vendor and approver cards, follow-up drafts and the
approval hash. Sentences come from templates.yaml; nothing here decides anything."""
import hashlib
import time
from datetime import datetime, timezone

from cc.core import load_templates


class _Blank(dict):
    def __missing__(self, key):
        return "—"


def _fact(e: dict) -> dict:
    return {k: e[k] for k in ("fact_id", "value", "source", "field_path", "retrieved_at", "call_id", "mock")}


def _values(record: dict, evidence: list[dict], item: dict) -> dict:
    """Placeholder values: every fact (fact_id with . -> _), the gap, and the carrier's GSTIN and name."""
    vals = {e["fact_id"].replace(".", "_"): e["value"] for e in evidence}
    vals.update(gap=item.get("gap") or "—", gstin=record["vendor"].get("gstin") or "—",
                name=record["vendor"].get("name_as_written") or "—")
    return vals


def _sentence(templates: dict, rule_id: str, key: str, values: dict) -> str | None:
    t = templates["rules"].get(rule_id, {}).get(key)
    return t.format_map(_Blank(values)) if t else None


def _texts(templates: dict, rule_id: str, values: dict) -> dict:
    texts = {f"{k}_{lang}": _sentence(templates, rule_id, f"{k}_{lang}" if k != "text" else lang, values)
             for k in ("text", "contest", "ask") for lang in ("en", "hi")}
    return {**texts, "internal": bool(templates["rules"].get(rule_id, {}).get("internal"))}


def serialize(job: dict, record: dict, decision: dict, evidence: list[dict], calls: list[dict],
              rules_hash: str) -> dict:
    """Output JSON (overview §4.4). Each reason and gap carries its facts and its sentences in both languages."""
    templates = load_templates()
    by_id = {e["fact_id"]: e for e in evidence}
    reasons = [{"check": r["check"], "rule_id": r["rule_id"], **_texts(templates, r["rule_id"], _values(record, evidence, r)),
                "facts": [_fact(by_id[f]) for f in r["facts"] if f in by_id]} for r in decision["reasons"]]
    gaps = [{"check": g["check"], "rule_id": g["rule_id"], "missing": g["gap"], "sources_tried": g["sources_tried"],
             **_texts(templates, g["rule_id"], _values(record, evidence, g))} for g in decision["gaps"]]
    now = time.time()
    return {
        "job_id": job["job_id"],
        "verdict": decision["verdict"],
        "reasons": reasons,
        "gaps": gaps,
        "flagged_lines": record.get("flagged_lines", []),
        "parse_notes": record.get("parse_notes", []),
        "language": record.get("language", "en"),
        "hindi_reviewed": bool(templates.get("hindi_reviewed")),
        "cost_inr": round(sum(c["cost_inr"] * (1 + c["repeats"]) for c in calls), 2),
        "elapsed_s": round(now - (job["started_at"] or now), 1),
        "rules_hash": rules_hash,
        "decided_at": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
        "mock": any(e["mock"] for e in evidence),
    }


def vendor_card(out: dict, lang: str) -> dict:
    """What the carrier is told: the verdict, each reason with its source and date, and how to contest."""
    reasons = [{"text": r[f"text_{lang}"], "contest": r[f"contest_{lang}"],
                "sources": [{"source": f["source"], "retrieved_at": f["retrieved_at"]} for f in r["facts"]]}
               for r in out["reasons"] if not r["internal"]]
    missing = ([g["missing"] for g in out["gaps"] if not g["internal"] and g["missing"]]
               if out["verdict"] == "Cannot verify" else [])
    return {"verdict": out["verdict"], "reasons": reasons, "missing": missing,
            "reviewed": lang == "en" or out["hindi_reviewed"]}


def follow_ups(out: dict, lang: str) -> list[str]:
    """One drafted ask per Cannot verify item. Shown with a copy button; never sent."""
    asks = []
    for g in out["gaps"]:
        a = None if g["internal"] else g.get(f"ask_{lang}")
        if a and a not in asks:
            asks.append(a)
    return asks


def approver_card(out: dict, record: dict, checks: list[dict]) -> dict:
    v, b = record["vendor"], record["bank"]
    return {
        "verdict": out["verdict"],
        "carrier": v.get("name_as_written"),
        "gstin": v.get("gstin"),
        "pan_holder_type": v.get("pan_holder_type"),
        "bank": {"ifsc": b.get("ifsc"), "account_last4": b.get("account_last4"), "holder_checked": False,
                 "note": "Account holder not verified"},
        "checks": [{"check": c["check"], "result": c["result"], "rule_id": c["rule_id"]} for c in checks],
        "reasons": out["reasons"],
        "gaps": out["gaps"],
        "notes": out["parse_notes"],
        "flagged_lines": out["flagged_lines"],
        "cost_inr": out["cost_inr"],
        "mock": out["mock"],
    }


def cards(out: dict, record: dict, checks: list[dict]) -> dict:
    return {"approver": approver_card(out, record, checks),
            "vendor": {lang: vendor_card(out, lang) for lang in ("en", "hi")},
            "asks": {lang: follow_ups(out, lang) for lang in ("en", "hi")}}


def approval_hash(verdict: str, evidence_ids: list[int], bank_hash: str | None, rules_hash: str) -> str:
    """sha256(verdict + evidence ids + bank hash + rules hash). Any change voids an approval."""
    parts = [verdict, ",".join(str(i) for i in sorted(evidence_ids)), bank_hash or "", rules_hash]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()
