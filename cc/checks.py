"""The validator, the checks and decide(). c1 is in intake; c2-c7 and c9 here."""
import json
from datetime import datetime, timezone

from cc.core import CheckResult
from cc.intake import pan_from_gstin
from cc.resolve import matches_gst, name_match
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
        return CheckResult(check="c2", result="cannot_verify", rule_id=gather.get("limit_rule", "not_checked"),
                           gap=gather["skipped"])
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


def _vals(evidence: list[dict], prefix: str = "gst.") -> dict:
    return {e["fact_id"][len(prefix):]: e["value"] for e in evidence if e["fact_id"].startswith(prefix)}


def c3(evidence: list[dict], resolve: dict, record: dict, rules: dict) -> CheckResult:
    """Who is this. One GST-confirmed identity passes; several PANs, an Identify tie (name only), a name that
    matches neither GST name, or acquired/merged give Refer. Lookalikes on a GST-confirmed full request are listed only."""
    cutoff = rules["thresholds"]["fuzzy_cutoff"]
    v = record["vendor"]
    have = {e["fact_id"] for e in evidence}
    tried = sorted({e["source"] for e in evidence if e["fact_id"].startswith(("gst.", "cand.", "search.", "crustdata."))})

    def r(result, rule_id, facts=(), gap=None):
        return CheckResult(check="c3", result=result, rule_id=rule_id, facts=[f for f in facts if f in have],
                           gap=gap, sources_tried=tried)

    if resolve.get("outcome") == "skipped":
        return r("cannot_verify", "not_checked", gap=resolve.get("note"))
    if resolve["mode"] == "discovery":
        if resolve["outcome"] == "several_pans":
            return r("refer", "several_pans", [f"{c['fact_prefix']}.legal_name" for c in resolve["candidates"]]
                     + [f"{c['fact_prefix']}.trade_name" for c in resolve["candidates"]])
        if resolve["outcome"] == "none":
            return r("cannot_verify", "discovery_none",
                     gap=resolve.get("note") or "GSTIN: no GST-confirmed company found for this name; ask the vendor")
        if resolve.get("identify_tie"):
            return r("refer", "identify_tie", [l["fact_id"] for l in resolve.get("lookalikes", [])])
    gst = _vals(evidence)
    if "status" not in gst or gst.get("valid") is False:
        return r("cannot_verify", "identity_unconfirmed", gap="GST did not confirm this GSTIN")
    if resolve.get("acquired"):
        return r("refer", "acquired_or_merged", [resolve["acquired"]])
    if resolve["mode"] == "discovery":
        return r("pass", "identity_pan_level",
                 ["gst.legal_name", "gst.trade_name"] + [f"search.{g}" for g in resolve.get("other_gstins", [])])
    name, pan = v.get("name_as_written"), pan_from_gstin(v["gstin"])
    if name and not matches_gst(name, gst.get("legal_name"), gst.get("trade_name"), pan, cutoff):
        return r("refer", "name_mismatch", ["gst.legal_name", "gst.trade_name"])
    return r("pass", "identity_confirmed", ["gst.trade_name"] if pan[3] == "P" else ["gst.legal_name"])


def c4(evidence: list[dict], gather: dict, record: dict, gstin: str | None, rules: dict) -> CheckResult:
    """Duplicates against the vendor master (read fresh) and earlier jobs. Another name under the same GSTIN or PAN,
    or the same GSTIN with a new bank account, gives Refer citing the row or job. Same PAN, other GSTIN: a branch."""
    if gather.get("skipped") or not gstin:
        return CheckResult(check="c4", result="cannot_verify", rule_id="not_checked",
                           gap=gather.get("skipped") or "no GSTIN to compare")
    cutoff = rules["thresholds"]["fuzzy_cutoff"]
    pan, name, bank = pan_from_gstin(gstin), record["vendor"].get("name_as_written"), record["bank"].get("account_hash")
    other_name, bank_changed, branch, known, tried = [], [], [], [], set()
    for e in evidence:
        fid, row = e["fact_id"], e["value"]
        if fid.startswith("vendor_master."):
            names, row_bank = [row.get("legal_name"), row.get("trade_name")], row.get("account_hash")
        elif fid.startswith("earlier_job."):
            names, row_bank = [row.get("name")], row.get("bank_hash")
        else:
            continue
        tried.add(e["source"])
        same_name = not name or not any(names) or any(name_match(name, n, cutoff) for n in names)
        if not same_name:
            other_name.append(fid)
        if row.get("gstin") == gstin and bank and row_bank and row_bank != bank:
            bank_changed.append(fid)
        if row.get("gstin") != gstin and row.get("pan") == pan:
            branch.append(fid)
        elif row.get("gstin") == gstin and same_name:
            known.append(fid)
    tried = sorted(tried)
    if other_name:
        return CheckResult(check="c4", result="refer", rule_id="dup_other_name", facts=other_name, sources_tried=tried)
    if bank_changed:
        return CheckResult(check="c4", result="refer", rule_id="bank_changed", facts=bank_changed, sources_tried=tried)
    if branch:
        return CheckResult(check="c4", result="pass", rule_id="dup_branch", facts=branch, sources_tried=tried)
    if known:
        return CheckResult(check="c4", result="pass", rule_id="dup_known_vendor", facts=known, sources_tried=tried)
    return CheckResult(check="c4", result="pass", rule_id="dup_none", sources_tried=tried)


def c9(evidence: list[dict], gather: dict, record: dict, gstin: str | None, rules: dict) -> CheckResult:
    """Bank: the IFSC exists, and the payee name matches the GST legal or trade name. The account holder is not
    checked. No bank details at all is a gap, not a block (phase 2 decision)."""
    b = record["bank"]
    if not (b.get("ifsc") or b.get("account_hash") or b.get("payee_name")):
        return CheckResult(check="c9", result="pass", rule_id="bank_not_given",
                           gap="Bank details not given; check before first payment")
    if not b.get("ifsc"):
        return CheckResult(check="c9", result="cannot_verify", rule_id="ifsc_missing", gap="IFSC")
    if gather.get("skipped"):
        return CheckResult(check="c9", result="cannot_verify", rule_id="not_checked", gap=gather["skipped"])
    tried = gather.get("ifsc_tried", [])
    if gather.get("ifsc_unavailable"):
        return CheckResult(check="c9", result="cannot_verify", rule_id="source_unavailable",
                           gap="IFSC source unavailable", sources_tried=tried)
    ifsc = _vals(evidence, "ifsc.")
    if ifsc.get("found") is not True:
        return CheckResult(check="c9", result="cannot_verify", rule_id="ifsc_not_found", facts=["ifsc.found"],
                           gap="IFSC not found; ask the vendor to recheck it", sources_tried=tried)
    if not b.get("payee_name"):
        return CheckResult(check="c9", result="cannot_verify", rule_id="payee_missing", facts=["ifsc.bank"],
                           gap="Payee name", sources_tried=tried)
    gst = _vals(evidence)
    cutoff = rules["thresholds"]["fuzzy_cutoff"]
    if not (name_match(b["payee_name"], gst.get("legal_name"), cutoff)
            or name_match(b["payee_name"], gst.get("trade_name"), cutoff)):
        return CheckResult(check="c9", result="refer", rule_id="payee_mismatch",
                           facts=[f"gst.{k}" for k in ("legal_name", "trade_name") if k in gst],
                           sources_tried=tried)
    return CheckResult(check="c9", result="pass", rule_id="bank_ok", facts=["ifsc.bank", "ifsc.branch"],
                       sources_tried=tried)


# --- optional checks (c5-c7): they can add Refer; finding nothing is a gap, never a block ---

def _months_since(iso: str) -> float | None:
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - d).days / 30.44


def c5(evidence: list[dict], gather: dict, rules: dict) -> CheckResult:
    """Active: recent news, open roles or a live website pass. Crustdata status not active, or a listed website
    that is down with no other sign of life, gives Refer (dormant). No data at all is a gap."""
    if gather.get("skipped"):
        return CheckResult(check="c5", result="cannot_verify", rule_id="not_checked", gap=gather["skipped"])
    cd, site = _vals(evidence, "crustdata."), _vals(evidence, "site.")
    tried = sorted({e["source"] for e in evidence if e["fact_id"].startswith(("crustdata.", "site."))})
    months = rules["thresholds"]["activity_months"]

    def r(result, rule_id, facts=(), gap=None):
        return CheckResult(check="c5", result=result, rule_id=rule_id, facts=list(facts), gap=gap, sources_tried=tried)

    if cd.get("status") and str(cd["status"]).lower() != "active":
        return r("refer", "dormant", ["crustdata.status"])
    signals = [f"crustdata.{k}" for k, v in cd.items()
               if k.startswith("news.") and (_months_since(v) or months + 1) <= months]
    if (cd.get("openings") or 0) > 0:
        signals.append("crustdata.openings")
    if site.get("live") is True:
        signals.append("site.live")
    if signals:
        return r("pass", "active", signals)
    if site.get("live") is False:
        return r("refer", "dormant", ["site.live", "site.url"])
    if cd or site:
        return r("cannot_verify", "no_recent_activity",
                 gap=f"No news in {months} months, no open roles and no website found")
    return r("cannot_verify", "no_activity_data", gap="Not in Crustdata and no website: activity not checked")


def c6(evidence: list[dict], gather: dict, record: dict, rules: dict) -> CheckResult:
    """Age and address: GST registered under min_age_months, a GST state other than the one stated, or a website
    domain under min_age_months gives Refer (E4, E17). A GST record not updated in stale_after_months is a gap (E33)."""
    if gather.get("skipped"):
        return CheckResult(check="c6", result="cannot_verify", rule_id="not_checked", gap=gather["skipped"])
    th, gst, rdap = rules["thresholds"], _vals(evidence), _vals(evidence, "rdap.")
    tried = sorted({e["source"] for e in evidence if e["fact_id"] in ("gst.registered_on", "gst.state")
                    or e["fact_id"].startswith("rdap.")})

    def r(result, rule_id, facts=(), gap=None):
        return CheckResult(check="c6", result=result, rule_id=rule_id, facts=list(facts), gap=gap, sources_tried=tried)

    age = _months_since(gst["registered_on"]) if gst.get("registered_on") else None
    if age is not None and age < th["min_age_months"]:
        return r("refer", "gst_too_new", ["gst.registered_on"])
    stated = (record["vendor"].get("state") or "").strip().lower()
    if stated and gst.get("state") and str(gst["state"]).strip().lower() != stated:
        return r("refer", "state_mismatch", ["gst.state"])
    dom_age = _months_since(rdap["registered_on"]) if rdap.get("registered_on") else None
    if dom_age is not None and dom_age < th["min_age_months"]:
        return r("refer", "domain_too_new", ["rdap.registered_on"])
    if age is None:
        return r("cannot_verify", "gst_date_missing", [f for f in ("gst.state",) if "state" in gst],
                 gap="GST registration date not in the record")
    facts = ["gst.registered_on"] + (["gst.state"] if "state" in gst else []) + (["rdap.registered_on"] if rdap else [])
    upd = _months_since(gst["updated_on"]) if gst.get("updated_on") else None
    if upd is not None and upd > th["stale_after_months"]:
        return r("pass", "age_address_ok", facts + ["gst.updated_on"],
                 gap=f"GST record last updated {gst['updated_on']}; may be out of date")
    return r("pass", "age_address_ok", facts)


def c7(gather: dict, rules: dict) -> CheckResult:
    """People and news: a hit with anchors_to_refer or more anchors gives Refer with the evidence (E28). Fewer but
    at least 1 is a gap (E27). Hits with 0 anchors were never stored (E26)."""
    if gather.get("skipped"):
        return CheckResult(check="c7", result="cannot_verify", rule_id="not_checked", gap=gather["skipped"])
    p = gather.get("people") or {}
    tried, notes = p.get("sources_tried", []), p.get("notes", [])
    if not p.get("directors"):
        return CheckResult(check="c7", result="pass", rule_id="people_not_given", sources_tried=tried,
                           gap="Directors not given; people and news not checked")
    need = rules["thresholds"]["anchors_to_refer"]
    strong = [h["fact_id"] for h in p["hits"] if len(h["anchors"]) >= need]
    weak = [h["fact_id"] for h in p["hits"] if len(h["anchors"]) < need]
    if strong:
        return CheckResult(check="c7", result="refer", rule_id="people_adverse", facts=strong + weak,
                           sources_tried=tried)
    if weak:
        return CheckResult(check="c7", result="cannot_verify", rule_id="people_one_anchor", facts=weak,
                           gap="A news item may be about a director (one matching detail); check by hand",
                           sources_tried=tried)
    return CheckResult(check="c7", result="pass", rule_id="people_clear", sources_tried=tried,
                       gap="; ".join(notes) or None)


def not_built(check: str, phase: int) -> CheckResult:
    return CheckResult(check=check, result="cannot_verify", rule_id=NOT_BUILT, gap=f"{check} is built in phase {phase}")


def decide(results: list[CheckResult], rules: dict) -> dict:
    """Reject -> Refer -> Cannot verify -> Clear. A fail outside hard_fails counts as a risk signal (Refer)."""
    hard = set(rules["hard_fails"])
    by_check = {r.check: r for r in results}
    gaps = [r for r in results if r.result == "cannot_verify" or r.gap]  # a pass may carry a gap (bank_not_given)

    rejects = [r for r in results if r.result == "fail" and r.rule_id in hard]
    refers = [r for r in results if r.result == "refer" or (r.result == "fail" and r.rule_id not in hard)]
    missing = [c for c in rules["required_checks"] if c not in by_check]
    blocking = [r for r in gaps if r.result == "cannot_verify" and r.check in rules["required_checks"]]

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
