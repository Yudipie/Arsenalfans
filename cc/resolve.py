"""Who is this: name normalisation and matching, Crustdata identity, discovery (name -> GSTINs -> one PAN),
and screening the directors given (c7).

Identity is confirmed through GST, never by name alone. Discovery runs when no GSTIN was given.
"""
import json
import os
import re
from difflib import SequenceMatcher

from cc import crustdata
from cc.core import load_rules
from cc.intake import GST_STATES, gstin_ok, pan_from_gstin
from cc.sources import SIGNAL_PROVIDERS, RungOff, SourceUnavailable, call, fact, get_path, gst_lookup

_GSTIN_IN_TEXT = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")


# --- names (E9, E10) ---

def norm(name: str | None) -> str:
    s = (name or "").lower().replace("&", " and ")
    s = re.sub(r"\bm\s*/\s*s\b\.?", " ", s)
    s = re.sub(r"\bpvt\b\.?", "private", s)
    s = re.sub(r"\bltd\b\.?", "limited", s)
    s = re.sub(r"\bco\b\.?", "company", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(s.split())


def name_match(a: str | None, b: str | None, cutoff: float) -> bool:
    if not a or not b or b == "[personal]":
        return False
    return SequenceMatcher(None, norm(a), norm(b)).ratio() >= cutoff


def matches_gst(name: str | None, legal: str | None, trade: str | None, pan: str | None, cutoff: float) -> bool:
    """A proprietorship (PAN 4th letter P) is matched on its trade name; the owner's legal name is personal data."""
    if pan and len(pan) > 3 and pan[3] == "P":
        return name_match(name, trade, cutoff)
    return name_match(name, legal, cutoff) or name_match(name, trade, cutoff)


# --- Crustdata identity: lookalikes, Identify tie (E12), acquired or merged (E16). Optional: GST carries c3 (E13) ---

def _guarded(job_id, provider, request, rungs_off, notes):
    """One Crustdata call, or None when the rung is off, returns 403, or is unavailable. Never blocks."""
    rung = crustdata.PROVIDER_RUNG[provider]
    if rung in rungs_off or not crustdata.rung_on(provider):
        return None, None
    try:
        return call(job_id, [provider], request, crustdata.PROVIDERS)
    except RungOff:
        rungs_off.append(rung)
        notes.append(f"Crustdata {rung}: not on this key (403)")
    except SourceUnavailable:
        notes.append(f"Crustdata {rung}: unavailable")
    return None, None


def crustdata_identity(job_id: str, name: str, cutoff: float, rungs_off: list, notes: list) -> dict:
    out = {"facts": [], "lookalikes": [], "tie": False, "acquired": None, "website": None}
    row, resp = _guarded(job_id, "crustdata_identify", {"names": [name]}, rungs_off, notes)
    if not row:
        return out
    try:
        matches = resp["body"][0].get("matches") or []
    except (IndexError, KeyError, TypeError, AttributeError):
        matches = []
    if not matches:
        notes.append("Not in Crustdata; GST carries identity")
        return out
    named = []
    for i, m in enumerate(matches):
        mname = ((m.get("company_data") or {}).get("basic_info") or {}).get("name")
        if mname and name_match(name, mname, cutoff):
            path = f"body.0.matches.{i}.company_data.basic_info.name"
            out["facts"].append(fact(row, f"crustdata.match.{i}", mname, path))
            named.append((i, m, mname))
            out["lookalikes"].append({"name": mname, "confidence": m.get("confidence_score"),
                                      "fact_id": f"crustdata.match.{i}", "source": row["provider"]})
    if not named:
        return out
    top = max(m.get("confidence_score") or 0 for _, m, _ in named)
    ids = {(m.get("company_data") or {}).get("crustdata_company_id") for _, m, _ in named
           if (m.get("confidence_score") or 0) == top}
    out["tie"] = len(ids) > 1
    best_i, best, _ = next(x for x in named if (x[1].get("confidence_score") or 0) == top)
    cid = (best.get("company_data") or {}).get("crustdata_company_id")
    if cid and not out["tie"]:
        # One Enrich call serves c3 (acquired) and c5 (activity): the groups come from rules.yaml.
        groups = load_rules()["providers"]["crustdata"]["enrich_groups"]
        erow, eresp = _guarded(job_id, "crustdata_enrich", {"company_ids": [cid], "fields": groups}, rungs_off, notes)
        if erow:
            base = "body.0.matches.0.company_data"
            path = f"{base}.revenue.acquisition_status"
            try:
                status = get_path(eresp, path)
            except KeyError:
                status = None
            if status and re.search(r"acquir|merg", str(status), re.I):
                out["facts"].append(fact(erow, "crustdata.acquisition_status", status, path))
                out["acquired"] = "crustdata.acquisition_status"
            out["facts"] += _activity_facts(erow, eresp, base)
            out["website"] = next((f["value"] for f in out["facts"] if f["fact_id"] == "crustdata.website"), None)
    return out


def _activity_facts(row: dict, resp: dict, base: str) -> list[dict]:
    """c5 inputs from the Enrich response: company status, website, open roles, and the 3 latest news dates."""
    out = []
    for fid, sub in (("status", "basic_info.status"), ("website", "basic_info.website"),
                     ("openings", "hiring.openings_count")):
        try:
            v = get_path(resp, f"{base}.{sub}")
        except KeyError:
            continue
        if v is not None:
            out.append(fact(row, f"crustdata.{fid}", v, f"{base}.{sub}"))
    try:
        news = get_path(resp, f"{base}.news") or []
    except KeyError:
        news = []
    dated = sorted(((n.get("article_publish_date"), i) for i, n in enumerate(news) if n.get("article_publish_date")),
                   reverse=True)[:3]
    for d, i in dated:
        out.append(fact(row, f"crustdata.news.{i}", d, f"{base}.news.{i}.article_publish_date"))
    return out


# --- discovery (name only, or no GSTIN given) ---

def _search_gstins(job_id: str, name: str, rungs_off: list, notes: list) -> tuple[list[str], list[dict], set]:
    """GSTINs in stored search passages (then fetched pages), in order found. Each one is a quote-backed fact.
    Also returns the GSTINs whose passage contains the name, so those PANs get the paid GST calls first."""
    found, facts, named = [], [], set()

    def scan(row, text):
        has_name = norm(name) in norm(text)
        for g in _GSTIN_IN_TEXT.findall(text or ""):
            if gstin_ok(g):
                if has_name:
                    named.add(g)
                if g not in found:
                    found.append(g)
                    facts.append(fact(row, f"search.{g}", g, quote=g))

    # "<name> GST number" beat "<name> GSTIN" on live probes (VRL: 10 hits on one PAN vs 2 unrelated GSTINs).
    row, resp = _guarded(job_id, "crustdata_web_search", {"query": f"{name} GST number"}, rungs_off, notes)
    results = ((resp or {}).get("body") or {}).get("results") or [] if row else []
    for r in results:
        scan(row, " ".join(str(r.get(k) or "") for k in ("title", "snippet", "content")))
    if not found and results:
        urls = [r["url"] for r in results if r.get("url")][:2]
        if urls:
            frow, fresp = _guarded(job_id, "crustdata_web_fetch", {"urls": urls}, rungs_off, notes)
            for page in (fresp or {}).get("body") or [] if frow else []:
                scan(frow, page.get("content") if isinstance(page, dict) else "")
    return found, facts, named


def discover(job_id: str, rec: dict, rules: dict, over, rungs_off: list, notes: list) -> dict:
    name, state = rec["vendor"]["name_as_written"], rec["vendor"].get("state")
    th = rules["thresholds"]
    gstins, facts, named = _search_gstins(job_id, name, rungs_off, notes)

    by_pan: dict[str, list[str]] = {}
    for g in gstins:
        by_pan.setdefault(pan_from_gstin(g), []).append(g)
    # PANs whose passage names the carrier first (stable), so unrelated GSTINs on the page don't use up the GST calls.
    pans = sorted(by_pan, key=lambda p: not any(g in named for g in by_pan[p]))[: th["max_candidates"]]

    candidates = []
    for pan in pans:
        group = sorted(by_pan[pan], key=lambda g: not (state and GST_STATES.get(g[:2], "").lower() == state.lower()))
        chosen = None
        for g in group[: th.get("gst_tries_per_pan", 2)]:
            limit = over()
            if limit:
                notes.append(limit)
                break
            try:
                gf = gst_lookup(job_id, g, prefix=f"cand.{g}")
            except SourceUnavailable:
                notes.append(f"GST unavailable for {g}")
                continue
            vals = {f["fact_id"].rsplit(".", 1)[1]: f["value"] for f in gf}
            if vals.get("valid") is False or "status" not in vals:
                continue
            facts += gf
            chosen = {"gstin": g, "pan": pan, "status": vals.get("status"), "legal_name": vals.get("legal_name"),
                      "trade_name": vals.get("trade_name"), "state": vals.get("state"),
                      "fact_prefix": f"cand.{g}", "source": gf[0]["source"] if gf else None}
            if str(vals["status"]).lower().startswith("active"):
                break
        if not chosen:
            continue
        chosen["matched"] = matches_gst(name, chosen["legal_name"], chosen["trade_name"], pan, th["fuzzy_cutoff"])
        chosen["other_gstins"] = [g for g in by_pan[pan] if g != chosen["gstin"]]
        candidates.append(chosen)

    matched = [c for c in candidates if c["matched"]]
    if len(matched) == 1:
        outcome, out_gstins = "one_pan", [matched[0]["gstin"]]
    elif len(matched) > 1:
        outcome, out_gstins = "several_pans", []
    else:
        outcome, out_gstins = "none", []
    return {"outcome": outcome, "gstins": out_gstins, "candidates": matched,
            "other_gstins": matched[0]["other_gstins"] if len(matched) == 1 else [],
            "dropped_candidates": [c["gstin"] for c in candidates if not c["matched"]], "facts": facts}


def resolve(job_id: str, rec: dict, c1: dict, rules: dict, over) -> dict:
    """Returns the resolve step output, with "facts" for the step to validate and store."""
    gstin, name = rec["vendor"].get("gstin"), rec["vendor"].get("name_as_written")
    cutoff = rules["thresholds"]["fuzzy_cutoff"]
    rungs_off, notes = [], []
    base = {"candidates": [], "other_gstins": [], "lookalikes": [], "identify_tie": False, "acquired": None,
            "website": None, "rungs_off": rungs_off, "notes": notes, "facts": []}

    if gstin:
        if c1["result"] != "pass":
            return {**base, "mode": "given", "outcome": "skipped", "gstins": [], "note": "c1 did not pass: no paid call"}
        out = {**base, "mode": "given", "outcome": "given", "gstins": [gstin]}
    elif c1["rule_id"] == "gstin_missing" and name:
        limit = over()
        if limit:
            return {**base, "mode": "discovery", "outcome": "none", "gstins": [], "note": limit}
        d = discover(job_id, rec, rules, over, rungs_off, notes)
        out = {**base, "mode": "discovery", **d}
    else:
        return {**base, "mode": "discovery", "outcome": "skipped", "gstins": [],
                "note": "no GSTIN and no name: nothing to look up"}

    if name and not over():
        ident = crustdata_identity(job_id, name, cutoff, rungs_off, notes)
        out["facts"] = out["facts"] + ident["facts"]
        out.update(lookalikes=ident["lookalikes"], identify_tie=ident["tie"], acquired=ident["acquired"],
                   website=ident["website"])
    return out


# --- c7: screen each director given. A hit names the director next to an adverse term; anchors tie it to ours ---

_SUFFIX = re.compile(r"\b(private limited|limited|llp|company)$")


def _has(text: str, phrase: str) -> bool:
    return bool(phrase) and f" {norm(phrase)} " in f" {text} "


def anchors(text: str, rec: dict, din: str | None, roles: list[str]) -> list[str]:
    """Employer, city, DIN, role: each one found in the passage ties the hit closer to our director (E26-E28)."""
    t, v = norm(text), rec["vendor"]
    employer = _SUFFIX.sub("", norm(v.get("name_as_written"))).strip()
    out = []
    if _has(t, employer):
        out.append("employer")
    if _has(t, v.get("city") or ""):
        out.append("city")
    if din and din in text:
        out.append("din")
    if any(_has(t, r) for r in roles):
        out.append("role")
    return out


def screen_directors(job_id: str, directors: list[dict], rec: dict, rules: dict, day_ts: int, over) -> dict:
    """Web Search (news and web, location IN, from news_months before the job's day) and OpenSanctions per director.

    Hits with 1+ anchors become facts. A director with no anchored hit leaves nothing behind: the step purges
    that director's call rows (request and response). The output holds indexes and anchor names, never names.
    """
    sig, th = rules["signals"], rules["thresholds"]
    out = {"directors": 0, "hits": [], "purge": [], "notes": [], "sources_tried": [], "facts": []}
    start = int(day_ts) - th["news_months"] * 30 * 86400
    terms = " OR ".join(f'"{t}"' if " " in t else t for t in sig["adverse_terms"])
    sanctions_on = rules["providers"]["sanctions"].get("enabled") and os.getenv("OPENSANCTIONS_API_KEY")
    if rules["providers"]["sanctions"].get("enabled") and not sanctions_on:
        out["notes"].append("OpenSanctions: no API key; sanctions lists not checked")
    rungs_off = []
    for i, d in enumerate(directors):
        name = (d.get("name") or "").strip()
        if not name or name == "[personal]":
            continue
        limit = over()
        if limit:
            out["notes"].append(limit)
            break
        out["directors"] += 1
        found, rows = [], []  # (row, field_path, value, text, needs_adverse)
        row, resp = _guarded(job_id, "crustdata_web_search",
                             {"query": f'"{name}" ({terms})', "start_date": start, "sources": ["news", "web"]},
                             rungs_off, out["notes"])
        if row:
            rows.append(row)
            for j, r in enumerate(((resp or {}).get("body") or {}).get("results") or []):
                field = "snippet" if _has(norm(r.get("snippet")), name) else "title"
                text = f"{r.get('title') or ''} {r.get('snippet') or ''}"
                found.append((row, f"body.results.{j}.{field}", r.get(field), text, True))
        if sanctions_on:
            try:
                srow, sresp = call(job_id, ["opensanctions"], {"name": name}, SIGNAL_PROVIDERS)
            except (RungOff, SourceUnavailable):
                out["notes"].append("OpenSanctions unavailable")
            else:
                rows.append(srow)
                for j, r in enumerate(sresp.get("results") or []):
                    text = f"{r.get('caption') or ''} {json.dumps(r.get('properties') or {}, ensure_ascii=False)}"
                    found.append((srow, f"results.{j}.caption", r.get("caption"), text, False))
        anchored = 0
        for row_, path, value, text, needs_adverse in found:
            if not value or not _has(norm(text), name):
                continue
            if needs_adverse and not any(_has(norm(text), t) for t in sig["adverse_terms"]):
                continue
            a = anchors(text, rec, d.get("din"), sig["roles"])
            if not a:
                continue  # a namesake: E26
            fid = f"people.d{i}.h{len(out['hits'])}"
            out["facts"].append(fact(row_, fid, value, path))
            out["hits"].append({"director": i, "fact_id": fid, "anchors": a, "source": row_["provider"]})
            anchored += 1
        if not anchored:
            out["purge"] += [r["call_id"] for r in rows if r["job_id"] == job_id]
        out["sources_tried"] = sorted({*out["sources_tried"], *(r["provider"] for r in rows)})
    return out
