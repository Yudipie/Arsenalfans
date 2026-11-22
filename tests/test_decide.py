"""Decide: rules shape and expected verdicts. Decision-order tests arrive in phase 1.

The test_phase0_gate_* tests stay red until phase 0 is really done; each names what is missing.
"""
import json

import pytest

from cc.core import LOGS, MODEL, ROOT, load_rules, mock
from cc.crustdata import ENRICH_GROUPS, RUNG_PATHS
from cc.sources import GST_FIXTURES, gst_fixture

VERDICTS = {"Clear", "Refer", "Reject", "Cannot verify"}
# Overview §5.
EXPECTED = {1: "Clear", 2: "Clear", 3: "Clear", 4: "Reject", 5: "Refer",
            6: "Refer", 7: "Refer", 8: "Cannot verify", 9: "Clear"}


def load_case(n: int) -> dict:
    return json.loads((ROOT / "tests" / "cases" / f"case_{n:02d}.json").read_text())


# --- rules.yaml ---

def test_rules_shape():
    r = load_rules()
    assert {"providers", "required_checks", "hard_fails", "thresholds", "prices_inr", "limits",
            "default_language"} <= r.keys()
    assert r["required_checks"] == ["c1", "c2", "c3", "c4", "c9"]
    assert {"gst_cancelled", "gst_suspended"} <= set(r["hard_fails"])
    for k in ("fuzzy_cutoff", "anchors_to_refer", "min_age_months", "max_candidates"):
        assert isinstance(r["thresholds"][k], (int, float)), k
    for k in ("cost_cap_inr", "time_limit_s"):
        assert isinstance(r["limits"][k], (int, float)), k
    assert all(isinstance(v, (int, float)) for v in r["prices_inr"].values())
    assert r["default_language"] in {"en", "hi"}


def test_enrich_groups_are_real_crustdata_groups():
    groups = set(load_rules()["providers"]["crustdata"]["enrich_groups"])
    assert groups <= ENRICH_GROUPS, f"unknown groups give a 400: {groups - ENRICH_GROUPS}"


def test_rungs_cover_every_crustdata_endpoint():
    assert set(load_rules()["providers"]["crustdata"]["rungs"]) == set(RUNG_PATHS)


def test_fixture_provider_only_with_mock():
    gst = load_rules()["providers"]["gst"]
    assert gst, "at least one GST provider"
    if mock():
        assert "fixture" in gst
    else:
        assert "fixture" not in gst, "MOCK=0: remove 'fixture' from providers.gst"


def test_gst_fixtures_are_tagged_and_complete():
    if not mock():
        pytest.skip("MOCK=0")
    for gstin in GST_FIXTURES:
        f = gst_fixture(gstin)
        assert f["mock"] is True
        for k in ("GSTIN", "gst_in_status", "legal_name_of_business", "trade_name_of_business",
                  "date_of_registration", "principal_place_split_address"):
            assert k in f, f"{gstin}: {k}"
        assert f["principal_place_split_address"]["state"]
    assert gst_fixture("27AAACR2504F1ZU")["gst_in_status"] == "Active"        # case 2
    assert "cancel" in gst_fixture("09AJOPN5622Q1ZC")["gst_in_status"].lower()  # case 4


def test_cases_expect_the_overview_verdicts():
    for n, verdict in EXPECTED.items():
        assert load_case(n)["expected"] == verdict, f"case {n}"
        assert verdict in VERDICTS


# --- phase 0 gate: red until done ---

def _probe(name: str) -> dict:
    path = LOGS / name
    if not path.exists():
        pytest.fail(f"missing logs/{name}: run the probe (see the module docstring)")
    return json.loads(path.read_text())


def test_phase0_gate_haiku_answers_live():
    p = _probe("probe_haiku.json")  # uv run python -m cc.sources (needs ANTHROPIC_API_KEY)
    assert p["model"].startswith(MODEL)
    assert p["text"].strip()
    assert p["ratelimit"], "rate-limit headers not captured"


def test_phase0_gate_crustdata_rungs_match_the_key():
    endpoints = _probe("probe_crustdata_endpoints.json")  # uv run python -m cc.crustdata
    status = {e["path"]: e.get("status") for e in endpoints.get("endpoints", [])}
    rungs = load_rules()["providers"]["crustdata"]["rungs"]
    for rung, path in RUNG_PATHS.items():
        assert rungs[rung] == (status.get(path) == "enabled"), f"rules.yaml rungs.{rung} disagrees with the key"


def test_phase0_gate_crustdata_hit_rate_recorded():
    s = _probe("probe_crustdata_summary.json")
    assert len(s["carriers"]) == 10
    assert s["hit_rate"] > 0, "no carrier matched: check the key and the request"
    assert "credits_used" in s


def test_phase0_gate_gst_live_or_mock():
    if (LOGS / "probe_gst_live.json").exists():
        assert _probe("probe_gst_live.json")["response"].get("GSTIN")
    else:
        assert mock() and GST_FIXTURES, "no live GST response and MOCK is not 1"


def test_phase0_gate_carriers_confirmed():
    pending = [n for n in range(1, 10) if load_case(n)["status"] != "confirmed"]
    assert not pending, f"confirm the carriers for cases {pending} and set status: confirmed"


# --- phase 1: decision order ---

from cc import checks, output, sources, store, worker  # noqa: E402
from cc.core import CheckResult  # noqa: E402


def cr(check, result, rule_id="x"):
    return CheckResult(check=check, result=result, rule_id=rule_id)


def passing(*skip):
    return [cr(c, "pass") for c in ("c1", "c2", "c3", "c4", "c9") if c not in skip]


def test_all_required_pass_is_clear():
    assert checks.decide(passing(), load_rules())["verdict"] == "Clear"


def test_hard_fail_beats_refer():
    rs = passing("c2", "c3") + [cr("c2", "fail", "gst_cancelled"), cr("c3", "refer")]
    assert checks.decide(rs, load_rules())["verdict"] == "Reject"


def test_refer_beats_cannot_verify():
    rs = passing("c3", "c9") + [cr("c3", "refer"), cr("c9", "cannot_verify")]
    assert checks.decide(rs, load_rules())["verdict"] == "Refer"


def test_required_cannot_verify_blocks_clear():
    rs = passing("c9") + [cr("c9", "cannot_verify")]
    assert checks.decide(rs, load_rules())["verdict"] == "Cannot verify"


def test_missing_required_check_blocks_clear():
    assert checks.decide(passing("c4"), load_rules())["verdict"] == "Cannot verify"


def test_optional_cannot_verify_is_only_a_gap():
    d = checks.decide(passing() + [cr("c7", "cannot_verify")], load_rules())
    assert d["verdict"] == "Clear" and [g["check"] for g in d["gaps"]] == ["c7"]


def test_fail_outside_hard_fails_is_refer():
    rs = passing("c6") + [cr("c6", "fail", "too_young")]
    assert checks.decide(rs, load_rules())["verdict"] == "Refer"


# --- phase 1: validator (E32) ---

def test_validator_drops_facts_not_in_the_stored_response():
    raw = {1: {"gst_in_status": "Active", "addr": {"state": "Goa"}, "note": "registered in 2017"}}
    base = {"call_id": 1, "quote": None}
    facts = [
        {**base, "fact_id": "ok", "field_path": "gst_in_status", "value": "Active"},
        {**base, "fact_id": "nested", "field_path": "addr.state", "value": "Goa"},
        {**base, "fact_id": "wrong_value", "field_path": "gst_in_status", "value": "Cancelled"},
        {**base, "fact_id": "no_path", "field_path": "nope", "value": "Active"},
        {**base, "fact_id": "quote_ok", "field_path": None, "quote": "registered in 2017", "value": 2017},
        {**base, "fact_id": "quote_bad", "field_path": None, "quote": "registered in 2015", "value": 2015},
        {**base, "fact_id": "no_call", "call_id": 9, "field_path": "gst_in_status", "value": "Active"},
        {**base, "fact_id": "unsourced", "field_path": None, "value": "Active"},
    ]
    kept, dropped = checks.validate(facts, raw)
    assert [f["fact_id"] for f in kept] == ["ok", "nested", "quote_ok"]
    assert len(dropped) == 5


# --- phase 1: the pipeline on a fresh database ---

@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CC_DB", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("MOCK", "1")
    store.init()


def run_case(n):
    job_id = worker.submit(load_case(n)["request"])
    worker.run_job(job_id)
    return job_id, store.latest_verdict(job_id)


def current_hash(job_id):
    v = store.latest_verdict(job_id)
    ids = [e["evidence_id"] for e in store.evidence(job_id)]
    return output.approval_hash(v["verdict"], ids, store.job(job_id)["bank_hash"], v["rules_hash"])


def test_active_gstin_passes_c1_and_c2_with_sourced_status(db):
    job_id, v = run_case(2)
    res = {c["check"]: c["result"] for c in v["checks"]}
    assert res["c1"] == "pass" and res["c2"] == "pass"
    status = next(e for e in store.evidence(job_id) if e["fact_id"] == "gst.status")
    assert status["field_path"] == "gst_in_status" and status["mock"] is True and status["retrieved_at"]


def test_cancelled_gstin_rejects_citing_status_and_date(db):
    _, v = run_case(4)
    assert v["verdict"] == "Reject"
    [reason] = v["output"]["reasons"]
    assert reason["rule_id"] == "gst_cancelled"
    assert {f["fact_id"] for f in reason["facts"]} == {"gst.status", "gst.cancelled_on"}


def test_bad_format_makes_no_call(db):
    job_id, v = run_case(8)
    assert v["verdict"] == "Cannot verify" and store.job_calls(job_id) == []


def test_a_stored_call_is_reused_not_fetched_again(db, monkeypatch):
    run_case(2)
    monkeypatch.setitem(sources.GST_PROVIDERS, "fixture", lambda **_: pytest.fail("fetched again"))
    job_id = worker.submit({**load_case(2)["request"], "client_id": "other-client"})
    worker.run_job(job_id)
    assert store.latest_verdict(job_id)["checks"][1]["result"] == "pass"


def test_source_down_retries_then_cannot_verify(db, monkeypatch):
    tries = []
    monkeypatch.setitem(sources.GST_PROVIDERS, "fixture", lambda **_: tries.append(1) or 1 / 0)
    _, v = run_case(2)
    c2 = v["checks"][1]
    assert (c2["result"], c2["rule_id"], c2["sources_tried"]) == ("cannot_verify", "source_unavailable", ["fixture"])
    assert len(tries) == 2  # one retry


def test_approve_twice_gives_one_row_and_edit_voids_it(db):
    job_id, _ = run_case(2)
    h = current_hash(job_id)
    store.approve(job_id, h, "a")
    store.approve(job_id, h, "a")
    assert len(store.approvals(job_id)) == 1
    req = json.loads(store.job(job_id)["request"])
    req["fields"]["account_last4"] = "0000"
    store.edit(job_id, req)
    assert [a["void"] for a in store.approvals(job_id)] == [1]
    worker.run_job(job_id)
    assert current_hash(job_id) != h
