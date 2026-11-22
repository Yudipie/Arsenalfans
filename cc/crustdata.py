"""Crustdata API (version 2025-11-01). Phase 0: account endpoints, Identify, Enrich, and the probe.

Phase 2 routes these through sources.call() and adds Web Search / Web Fetch; phase 4 adds Person Search.
Probe: `uv run python -m cc.crustdata` (spends credits: 1 Identify call + 1 Enrich call).
"""
import json
import os

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
