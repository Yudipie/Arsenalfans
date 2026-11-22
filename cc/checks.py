"""The validator, the checks and decide(). Phase 1: c1 (in intake), c2, decide. c3, c4, c9 arrive in phase 2."""
import json

from cc.core import CheckResult
from cc.sources import get_path

# Required checks not built yet report this rule_id; eval.py counts such cases as pending, not wrong.
NOT_BUILT = "not_built"


def validate(facts: list[dict], responses: dict[int, dict]) -> tuple[list[dict], list[dict]]:
    """No source, no claim (E32). Keep a fact only if its field_path holds that value, or its quote appears
    verbatim, in the stored response it names. Returns (kept, dropped)."""
    kept, dropped = [], []
    for f in facts:
        raw = responses.get(f["call_id"])
        ok = False
        if raw is not None and f.get("field_path"):
            try:
                ok = get_path(raw, f["field_path"]) == f["value"]
            except KeyError:
                ok = False
        elif raw is not None and f.get("quote"):
            ok = f["quote"] in json.dumps(raw, ensure_ascii=False)
        (kept if ok else dropped).append(f)
    return kept, dropped


def c2(evidence: list[dict], gather: dict) -> CheckResult:
    """Exists and status, from the GST API."""
    tried = gather.get("sources_tried", [])
    if gather.get("skipped"):
        return CheckResult(check="c2", result="cannot_verify", rule_id="not_checked", gap=gather["skipped"])
    if gather.get("unavailable"):
        return CheckResult(check="c2", result="cannot_verify", rule_id="source_unavailable",
                           gap="GST source unavailable", sources_tried=tried)
    facts = {e["fact_id"]: e["value"] for e in evidence}
    if facts.get("gst.valid") is False or "gst.status" not in facts:
        return CheckResult(check="c2", result="cannot_verify", rule_id="gst_not_found", facts=["gst.valid"],
                           gap="GST has no record of this GSTIN; ask the vendor to recheck it", sources_tried=tried)
    status = str(facts["gst.status"]).lower()
    if status.startswith("active"):
        return CheckResult(check="c2", result="pass", rule_id="gst_active", facts=["gst.status"], sources_tried=tried)
    if "cancel" in status:
        return CheckResult(check="c2", result="fail", rule_id="gst_cancelled",
                           facts=[f for f in ("gst.status", "gst.cancelled_on") if f in facts], sources_tried=tried)
    if "suspend" in status:
        return CheckResult(check="c2", result="fail", rule_id="gst_suspended", facts=["gst.status"], sources_tried=tried)
    return CheckResult(check="c2", result="refer", rule_id="gst_status_other", facts=["gst.status"], sources_tried=tried)


def not_built(check: str, phase: int) -> CheckResult:
    return CheckResult(check=check, result="cannot_verify", rule_id=NOT_BUILT, gap=f"{check} is built in phase {phase}")


def decide(results: list[CheckResult], rules: dict) -> dict:
    """Reject -> Refer -> Cannot verify -> Clear. A fail outside hard_fails counts as a risk signal (Refer)."""
    hard = set(rules["hard_fails"])
    by_check = {r.check: r for r in results}
    gaps = [r for r in results if r.result == "cannot_verify"]

    rejects = [r for r in results if r.result == "fail" and r.rule_id in hard]
    refers = [r for r in results if r.result == "refer" or (r.result == "fail" and r.rule_id not in hard)]
    missing = [c for c in rules["required_checks"] if c not in by_check]
    blocking = [r for r in gaps if r.check in rules["required_checks"]]

    if rejects:
        verdict, reasons = "Reject", rejects
    elif refers:
        verdict, reasons = "Refer", refers
    elif blocking or missing:
        verdict, reasons = "Cannot verify", blocking
    else:
        verdict, reasons = "Clear", [r for r in results if r.result == "pass"]
    return {"verdict": verdict, "reasons": [r.model_dump() for r in reasons], "gaps": [r.model_dump() for r in gaps],
            "missing_checks": missing}
