"""Outside calls: the call() wrapper, GST providers (fixture, Cashfree), the vendor-master reader,
and the phase-0 Haiku/GST probes. IFSC, OpenSanctions, RDAP and MCA arrive in phases 2 and 4.
Probe: `uv run python -m cc.sources` writes logs/probe_haiku.json and logs/probe_gst_live.json.
"""
import copy
import csv
import hashlib
import json
import os
import time
from datetime import datetime, timezone

import httpx

from cc import store
from cc.core import LOGS, MODEL, ROOT, load_rules, mock

# Shape of Cashfree's POST /verification/gstin response (docs example, abridged).
# Used only with MOCK=1; every fact built from it is tagged mock.
GST_FIXTURES = {
    # Cashfree's documented sample, unchanged.
    "29AAICP2912R1ZR": {
        "reference_id": 19,
        "GSTIN": "29AAICP2912R1ZR",
        "legal_name_of_business": "UJJIVAN SMALL FINANCE BANK LIMITED",
        "trade_name_of_business": "UJJIVAN SMALL FINANCE BANK",
        "gst_in_status": "Active",
        "taxpayer_type": "Regular",
        "constitution_of_business": "Public Limited Company",
        "date_of_registration": "2017-09-30",
        "last_update_date": None,
        "principal_place_split_address": {"state": "Karnataka", "district": "Bengaluru Urban"},
        "valid": True,
        "message": "GSTIN Exists",
    },
    # Rehearsal case 2 (happy path), from public GST lookup pages; confirm live before stage.
    "27AAACR2504F1ZU": {
        "reference_id": 1002,
        "GSTIN": "27AAACR2504F1ZU",
        "legal_name_of_business": "RAJESH ROADLINES PVT LTD",
        "trade_name_of_business": "RAJESH ROADLINES PVT LTD",
        "gst_in_status": "Active",
        "taxpayer_type": "Regular",
        "constitution_of_business": "Private Limited Company",
        "date_of_registration": "2017-07-01",
        "last_update_date": None,
        "principal_place_split_address": {"state": "Maharashtra", "district": "Mumbai"},
        "valid": True,
        "message": "GSTIN Exists",
    },
    # Rehearsal case 4 (cancelled suo moto). Proprietor's legal name withheld: personal data.
    "09AJOPN5622Q1ZC": {
        "reference_id": 1004,
        "GSTIN": "09AJOPN5622Q1ZC",
        "legal_name_of_business": "[personal]",
        "trade_name_of_business": "SHREE SHYAM ROADLINES",
        "gst_in_status": "Cancelled suo-moto",
        "taxpayer_type": "Regular",
        "constitution_of_business": "Proprietorship",
        "date_of_registration": "2018-05-25",
        "date_of_cancellation": "2019-12-03",
        "last_update_date": None,
        "principal_place_split_address": {"state": "Uttar Pradesh", "district": "Kanpur Nagar"},
        "valid": True,
        "message": "GSTIN Exists",
    },
}


def gst_fixture(gstin: str) -> dict:
    """Fixture provider. Refuses to answer unless MOCK=1. An unknown GSTIN gets Cashfree's not-found shape."""
    if not mock():
        raise RuntimeError("fixture provider used with MOCK=0")
    resp = GST_FIXTURES.get(gstin) or {"GSTIN": gstin, "valid": False, "message": "GSTIN Doesn't Exist"}
    return {**copy.deepcopy(resp), "mock": True}


def gst_cashfree(gstin: str) -> dict:
    r = httpx.post("https://api.cashfree.com/verification/gstin",
                   headers={"x-client-id": os.environ["GST_CLIENT_ID"], "x-client-secret": os.environ["GST_CLIENT_SECRET"]},
                   json={"GSTIN": gstin}, timeout=15)
    r.raise_for_status()
    return r.json()


GST_PROVIDERS = {"fixture": gst_fixture, "cashfree": gst_cashfree}
PRICE_KEYS = {"cashfree": "cashfree_gstin"}  # provider -> rules.yaml prices_inr key; unlisted providers cost 0

# Cashfree-shaped response -> normalised facts. The fixture shares the shape.
GST_FIELDS = {
    "gst.valid": "valid",
    "gst.status": "gst_in_status",
    "gst.legal_name": "legal_name_of_business",
    "gst.trade_name": "trade_name_of_business",
    "gst.constitution": "constitution_of_business",
    "gst.state": "principal_place_split_address.state",
    "gst.district": "principal_place_split_address.district",
    "gst.registered_on": "date_of_registration",
    "gst.cancelled_on": "date_of_cancellation",
    "gst.updated_on": "last_update_date",
}


class SourceUnavailable(Exception):
    def __init__(self, sources_tried: list[str]):
        super().__init__(f"all sources failed: {sources_tried}")
        self.sources_tried = sources_tried


def call_key(provider: str, request: dict) -> str:
    return hashlib.sha256((provider + json.dumps(request, sort_keys=True)).encode()).hexdigest()


def _step_delay() -> None:
    """Test hook: CC_STEP_DELAY seconds of simulated latency, so the kill test can land mid-call."""
    time.sleep(float(os.getenv("CC_STEP_DELAY", "0")))


def call(job_id: str, providers: list[str], request: dict, fns: dict) -> tuple[dict, dict]:
    """Every outside call goes through here. Returns (calls row, response).

    1. Key = sha256(provider + request). A stored response for any listed provider is reused, never refetched.
       A stored mock response is reused only with MOCK=1.
    2. Otherwise try each provider in order (one fallback): timeout, 1 retry.
    3. The calls row is written before the request and finished after it, so a kill in flight is counted.
    """
    for p in providers:
        row = store.call_row(call_key(p, request))
        if row and row["status"] == "done" and (mock() or not row["mock"]):
            return row, json.loads(row["response"])
    prices = load_rules()["prices_inr"]
    tried = []
    for p in providers:
        key = call_key(p, request)
        tried.append(p)
        for _attempt in range(2):
            store.call_started(key, p, request, job_id)
            try:
                resp = fns[p](**request)
            except Exception as e:  # timeout, HTTP error, fixture refused
                store.call_finished(key, {"error": repr(e)}, 0.0, p == "fixture", status="error")
                continue
            _step_delay()
            store.call_finished(key, resp, prices.get(PRICE_KEYS.get(p), 0.0), p == "fixture")
            return store.call_row(key), resp
    raise SourceUnavailable(tried)


def get_path(data, path: str):
    """Dotted field path into a stored response. Raises KeyError if absent."""
    for part in path.split("."):
        if not isinstance(data, dict) or part not in data:
            raise KeyError(path)
        data = data[part]
    return data


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def gst_lookup(job_id: str, gstin: str) -> list[dict]:
    """GST status for one GSTIN, normalised to facts that each point at a field in the stored response."""
    row, resp = call(job_id, load_rules()["providers"]["gst"], {"gstin": gstin}, GST_PROVIDERS)
    facts = []
    for fact_id, path in GST_FIELDS.items():
        try:
            value = get_path(resp, path)
        except KeyError:
            continue
        if value is None:
            continue
        facts.append({"fact_id": fact_id, "value": value, "source": f"{row['provider']}_gstin", "field_path": path,
                      "quote": None, "retrieved_at": _iso(row["finished_at"]),
                      "record_updated_at": resp.get("last_update_date"), "call_id": row["call_id"],
                      "mock": bool(row["mock"])})
    return facts


def read_vendor_master() -> list[dict]:
    """Read fresh on every job; never cached."""
    with (ROOT / "data" / "vendor_master.csv").open() as f:
        return list(csv.DictReader(f))


def _save(name: str, data: dict) -> None:
    LOGS.mkdir(exist_ok=True)
    (LOGS / name).write_text(json.dumps(data, indent=2, default=str))
    print(f"wrote logs/{name}")


def probe_haiku() -> None:
    import anthropic

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("haiku: skipped, ANTHROPIC_API_KEY not set")
        return
    t0 = time.time()
    raw = anthropic.Anthropic().messages.with_raw_response.create(
        model=MODEL,
        max_tokens=256,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": "Reply with the single word OK."}],
    )
    msg = raw.parse()
    _save("probe_haiku.json", {
        "model": msg.model,
        "text": "".join(b.text for b in msg.content if b.type == "text"),
        "usage": msg.usage.model_dump(),
        "request_id": raw.headers.get("request-id"),
        "ratelimit": {k: v for k, v in raw.headers.items() if k.startswith("anthropic-ratelimit")},
        "elapsed_s": round(time.time() - t0, 2),
    })


def probe_gst(gstin: str = "27AAACR2504F1ZU") -> None:
    cid, secret = os.getenv("GST_CLIENT_ID"), os.getenv("GST_CLIENT_SECRET")
    if os.getenv("GST_PROVIDER", "cashfree") != "cashfree" or not (cid and secret):
        print("gst: skipped, no Cashfree GST_CLIENT_ID/GST_CLIENT_SECRET (MOCK=1 fixture in use)")
        return
    r = httpx.post(
        "https://api.cashfree.com/verification/gstin",
        headers={"x-client-id": cid, "x-client-secret": secret},
        json={"GSTIN": gstin},
        timeout=20,
    )
    r.raise_for_status()
    _save("probe_gst_live.json", {"provider": "cashfree", "gstin": gstin, "response": r.json()})


if __name__ == "__main__":
    probe_haiku()
    probe_gst()
