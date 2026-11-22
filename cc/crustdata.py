"""Crustdata API (version 2025-11-01): account endpoints, Identify, Enrich, Web Search, Web Fetch, and the probe.

PROVIDERS are the call()-ready versions: each returns {"body", "credits"} and raises RungOff on a 403.
Person Search is not wired: it can't raise a c7 signal on its own (phase 4 decision).
Probe: `uv run python -m cc.crustdata` (spends credits: 1 Identify call + 1 Enrich call).
"""
import json
import os
import time

import httpx

from cc.core import LOGS, load_rules

BASE = "https://api.crustdata.com"

# Rung name in rules.yaml -> endpoint path reported by GET /account/endpoints.
RUNG_PATHS = {
    "identify": "/company/identify",
    "enrich": "/company/enrich",
    "web_search": "/web/search/live",
    "web_fetch": "/web/enrich/live",
    "person_search": "/person/search",
}

# Valid Enrich `fields` groups, from the Enrich API reference.
ENRICH_GROUPS = {
    "basic_info", "revenue", "headcount", "funding", "hiring", "web_traffic", "seo", "competitors",
    "employee_reviews", "people", "locations", "taxonomy", "followers", "news", "software_reviews",
    "social_profiles", "reviews", "public_launches", "market_intel", "metadata",
    "crustdata_company_id", "updated_at", "indexed_at", "social_posts",
}

# Ten real Indian carriers for the phase-0 hit-rate probe: large listed ones and small ones.
PROBE_CARRIERS = [
    "VRL Logistics Limited",
    "Transport Corporation of India Limited",
    "Gati Limited",
    "Mahindra Logistics Limited",
    "Delhivery Limited",
    "Rajesh Roadlines Pvt Ltd",
    "Bhagwati Road Lines Private Limited",
    "Ahmedabad Roadlines Private Limited",
    "Rajasthan Logistics Private Limited",
    "Shree Ganesh Roadlines",
]


def _headers() -> dict:
    key = os.getenv("CRUSTDATA_API_KEY")
    if not key:
        raise RuntimeError("CRUSTDATA_API_KEY not set in .env")
    return {"authorization": f"Bearer {key}", "x-api-version": load_rules()["providers"]["crustdata"]["api_version"]}


def _credits(r: httpx.Response) -> float:
    return float(r.headers.get("x-credits-used", 0) or 0)


def account_endpoints() -> dict:
    r = httpx.get(f"{BASE}/account/endpoints", headers=_headers(), timeout=20)
    r.raise_for_status()
    return r.json()


def account_credits() -> dict:
    r = httpx.get(f"{BASE}/account/credits", headers=_headers(), timeout=20)
    r.raise_for_status()
    return r.json()


def identify(names: list[str]) -> tuple[list, float]:
    r = httpx.post(f"{BASE}/company/identify", headers=_headers(), json={"names": names}, timeout=60)
    r.raise_for_status()
    return r.json(), _credits(r)


def enrich(company_ids: list[int], fields: list[str]) -> tuple[list, float]:
    r = httpx.post(f"{BASE}/company/enrich", headers=_headers(),
                   json={"crustdata_company_ids": company_ids, "fields": fields}, timeout=60)
    r.raise_for_status()
    return r.json(), _credits(r)


# --- call()-ready providers (phase 2) ---

def _post(path: str, body: dict) -> dict:
    from cc.sources import RungOff  # sources imports nothing from here; local import keeps it that way

    r = httpx.post(f"{BASE}{path}", headers=_headers(), json=body, timeout=30)
    if r.status_code == 403:
        raise RungOff(path)
    if r.status_code == 429:  # rate limited: wait it out, then let call()'s one retry go
        time.sleep(min(float(r.headers.get("retry-after") or 2), 5))
    r.raise_for_status()
    return {"body": r.json(), "credits": _credits(r)}


PROVIDERS = {
    "crustdata_identify": lambda names: _post("/company/identify", {"names": names}),
    "crustdata_enrich": lambda company_ids, fields: _post(
        "/company/enrich", {"crustdata_company_ids": company_ids, "fields": fields}),
    # start_date: unix seconds (docs: Web Search reference). c7 passes the job's day, so the call key is stable.
    "crustdata_web_search": lambda query, start_date=None, sources=("web",): _post(
        "/web/search/live", {"query": query, "sources": list(sources), "location": "IN",
                             **({"start_date": start_date} if start_date else {})}),
    "crustdata_web_fetch": lambda urls: _post("/web/enrich/live", {"urls": urls}),
}
PROVIDER_RUNG = {"crustdata_identify": "identify", "crustdata_enrich": "enrich",
                 "crustdata_web_search": "web_search", "crustdata_web_fetch": "web_fetch"}


def rung_on(provider: str) -> bool:
    cd = load_rules()["providers"]["crustdata"]
    return bool(cd.get("enabled")) and bool(cd["rungs"].get(PROVIDER_RUNG[provider]))


def enabled_rungs(endpoints: dict) -> dict:
    status = {e["path"]: e.get("status") for e in endpoints.get("endpoints", [])}
    return {rung: status.get(path) == "enabled" for rung, path in RUNG_PATHS.items()}


def probe() -> None:
    LOGS.mkdir(exist_ok=True)
    groups = load_rules()["providers"]["crustdata"]["enrich_groups"]

    endpoints = account_endpoints()
    (LOGS / "probe_crustdata_endpoints.json").write_text(json.dumps(endpoints, indent=2))
    before = account_credits()

    found, credits = identify(PROBE_CARRIERS)
    rows = []
    for name, entry in zip(PROBE_CARRIERS, found):
        top = max(entry.get("matches") or [], key=lambda m: m.get("confidence_score", 0), default=None)
        data = (top or {}).get("company_data", {})
        rows.append({
            "name": name,
            "matched": top is not None,
            "confidence": top and top.get("confidence_score"),
            "crustdata_company_id": data.get("crustdata_company_id"),
            "matched_name": data.get("basic_info", {}).get("name"),
        })

    ids = [r["crustdata_company_id"] for r in rows if r["crustdata_company_id"]]
    enriched, enrich_credits = enrich(ids, groups) if ids else ([], 0.0)
    credits += enrich_credits
    by_id = {}
    for entry in enriched:
        for m in entry.get("matches", []):
            cd = m.get("company_data", {})
            by_id[cd.get("crustdata_company_id")] = sorted(k for k, v in cd.items() if v)
    for r in rows:
        r["groups_returned"] = by_id.get(r["crustdata_company_id"], [])

    summary = {
        "carriers": rows,
        "hit_rate": sum(r["matched"] for r in rows) / len(rows),
        "groups_requested": groups,
        "credits_used": credits,
        "credits_before": before.get("account", {}).get("credits"),
        "suggested_rungs": enabled_rungs(endpoints),
    }
    (LOGS / "probe_crustdata_raw.json").write_text(json.dumps({"identify": found, "enrich": enriched}, indent=2))
    (LOGS / "probe_crustdata_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"hit rate {summary['hit_rate']:.0%}, credits used {credits}")
    print("set providers.crustdata.rungs in rules.yaml to:", summary["suggested_rungs"])


if __name__ == "__main__":
    probe()
