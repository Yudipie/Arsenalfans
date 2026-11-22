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


@pytest.mark.phase3
def test_bad_format_makes_no_gst_call(db):
    job_id, v = run_case(8)
    assert v["verdict"] == "Cannot verify"
    assert [c for c in store.job_calls(job_id) if "gstin" in json.loads(c["request"])] == []


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


# --- phase 3: templates and cards ---

import re  # noqa: E402

from cc.core import load_templates  # noqa: E402


def used_rule_ids() -> set[str]:
    ids = set()
    for f in ("checks.py", "intake.py", "worker.py"):
        ids |= set(re.findall(r'rule_id="(\w+)"', (ROOT / "cc" / f).read_text(encoding="utf-8")))
        ids |= set(re.findall(r'cv\("(\w+)"', (ROOT / "cc" / f).read_text(encoding="utf-8")))
    return ids | {"over_cost_cap", "over_time_limit"}


@pytest.mark.phase3
def test_every_rule_id_has_both_sentences():
    rules = load_templates()["rules"]
    for rid in used_rule_ids():
        assert rid in rules, f"templates.yaml has no entry for {rid}"
        assert rules[rid].get("en") and rules[rid].get("hi"), rid


@pytest.mark.phase3
def test_templates_use_placeholders_only_and_hindi_is_flagged():
    t = load_templates()
    assert t["hindi_reviewed"] is False
    for rid, entry in t["rules"].items():
        for k, s in entry.items():
            if isinstance(s, str):
                assert not re.search(r"\{[^}]*[^\w}][^}]*\}", s), f"{rid}.{k}: placeholders only"


@pytest.mark.phase3
def test_cannot_verify_rules_have_contest_and_ask():
    rules = load_templates()["rules"]
    for rid in used_rule_ids() - {"formats_ok", "gst_active"}:
        for k in ("contest_en", "contest_hi"):
            assert rules[rid].get(k), f"{rid}.{k}"


@pytest.mark.phase3
@pytest.mark.parametrize("n, verdict", [(4, "Reject"), (8, "Cannot verify")])
def test_vendor_cards_in_both_languages(db, monkeypatch, n, verdict):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _, v = run_case(n)
    assert v["verdict"] == verdict
    for lang in ("en", "hi"):
        vc = v["card"]["vendor"][lang]
        assert vc["verdict"] == verdict and vc["reasons"]
        assert all(r["text"] and r["contest"] and "—" not in r["text"] for r in vc["reasons"])
    if n == 4:
        r = v["card"]["vendor"]["en"]["reasons"][0]
        assert "2019-12-03" in r["text"] and r["sources"][0]["source"] == "fixture_gstin" and r["sources"][0]["retrieved_at"]
        assert "REG-22" in r["contest"]
    assert v["card"]["vendor"]["hi"]["reviewed"] is False


@pytest.mark.phase3
def test_refer_renders_with_source(db, monkeypatch):
    monkeypatch.setitem(sources.GST_FIXTURES, "27AAACR2504F1ZU",
                        {**sources.GST_FIXTURES["27AAACR2504F1ZU"], "gst_in_status": "Provisional"})
    _, v = run_case(2)
    reason = next(r for r in v["output"]["reasons"] if r["rule_id"] == "gst_status_other")
    assert "Provisional" in reason["text_en"] and "Provisional" in reason["text_hi"] and reason["facts"]
    assert v["card"]["approver"]["bank"]["note"] == "Account holder not verified"


# --- phase 4-B: cost, limits, redecide ---

@pytest.mark.phase4b
def test_job_cost_is_the_sum_of_its_calls(db, monkeypatch):
    monkeypatch.setitem(sources.PRICE_KEYS, "fixture", "cashfree_gstin")  # give the fixture a price
    monkeypatch.setitem(sources.PARSE_PROVIDERS, "haiku",
                        lambda **_: ({"text": json.dumps({"name": "Ashirwad Roadways", "gstin": "23AKHPK8O71C1ZE"})},
                                     {"tokens_in": 1_000_000, "tokens_out": 100_000}))
    for n in (2, 8):
        job_id, v = run_case(n)
        calls = store.job_calls(job_id)
        assert v["output"]["cost_inr"] == round(sum(c["cost_inr"] * (1 + c["repeats"]) for c in calls), 2)
        assert v["card"]["approver"]["cost_inr"] == v["output"]["cost_inr"] > 0
    p = load_rules()["prices_inr"]
    haiku = next(c for c in store.job_calls(job_id) if c["provider"] == "haiku")
    assert (haiku["tokens_in"], haiku["tokens_out"]) == (1_000_000, 100_000)
    assert haiku["cost_inr"] == pytest.approx(p["haiku_in_per_mtok"] + p["haiku_out_per_mtok"] / 10)


@pytest.mark.phase4b
def test_cost_cap_turns_remaining_checks_into_cannot_verify(db, monkeypatch):
    rules = load_rules()
    rules["limits"]["cost_cap_inr"] = 0.5
    monkeypatch.setattr(sources, "load_rules", lambda: rules)
    monkeypatch.setitem(sources.PRICE_KEYS, "fixture", "cashfree_gstin")  # ₹1 > ₹0.5 cap
    job_id, v = run_case(2)
    c2 = next(c for c in v["checks"] if c["check"] == "c2")
    assert (c2["result"], c2["rule_id"]) == ("cannot_verify", "over_cost_cap")
    assert v["verdict"] == "Cannot verify" and store.job_calls(job_id) == []


@pytest.mark.phase4b
def test_time_limit_turns_remaining_checks_into_cannot_verify(db, monkeypatch):
    rules = load_rules()
    rules["limits"]["time_limit_s"] = -1
    monkeypatch.setattr(sources, "load_rules", lambda: rules)
    _, v = run_case(2)
    c2 = next(c for c in v["checks"] if c["check"] == "c2")
    assert c2["rule_id"] == "over_time_limit"


@pytest.mark.phase4b
def test_redecide_uses_stored_evidence_only_and_voids_on_hash_change(db, monkeypatch):
    job_id, v = run_case(4)
    h = current_hash(job_id)
    store.approve(job_id, h, "a")
    monkeypatch.setitem(sources.GST_PROVIDERS, "fixture", lambda **_: pytest.fail("fetched again"))
    worker.redecide(job_id)  # same rules: same hash, approval kept
    assert [a["void"] for a in store.approvals(job_id)] == [0]
    assert len(store.verdicts(job_id)) == 2
    monkeypatch.setattr(worker, "rules_hash", lambda: "edited-rules")
    out = worker.redecide(job_id)
    assert out["approval_hash"] != h
    assert [a["void"] for a in store.approvals(job_id)] == [1]
    hist = store.verdicts(job_id)
    assert len(hist) == 3 and hist[-1]["rules_hash"] == "edited-rules" and hist[-1]["verdict"] == "Reject"


@pytest.mark.phase4b
def test_reapproving_a_voided_hash_makes_it_live_again(db, monkeypatch):
    job_id, _ = run_case(4)
    h = current_hash(job_id)
    store.approve(job_id, h, "a")
    store.void_approvals_except(job_id, "rules-b-hash")  # redecide under rules B
    store.approve(job_id, h, "b")  # back on rules A, approved again
    rows = store.approvals(job_id)
    assert [(r["void"], r["approved_by"]) for r in rows] == [(0, "b")]


@pytest.mark.phase4b
def test_redecide_never_fetches(db, monkeypatch):
    job_id, _ = run_case(2)
    with store.tx() as c:  # pretend the stored GST call is gone; redecide must not refetch it
        c.execute("DELETE FROM calls")
    monkeypatch.setattr(sources, "OFFLINE", False)
    worker.redecide(job_id)
    assert sources.OFFLINE is False and store.job_calls(job_id) == []


# --- phase 4-B: cost units, limits on stored calls, offline reset, API ---

from fastapi.testclient import TestClient  # noqa: E402

from cc import app as app_module  # noqa: E402


@pytest.mark.phase4b
def test_cost_units():
    prices = {"cashfree_gstin": 1.0, "crustdata_credit": 0.5, "haiku_in_per_mtok": 8.5, "haiku_out_per_mtok": 42.5}
    assert sources._cost("cashfree", {}, prices) == 1.0
    assert sources._cost("fixture", {}, prices) == 0.0
    assert sources._cost("crustdata", {"credits": 4}, prices) == 2.0
    assert sources._cost("haiku", {"tokens_in": 2000, "tokens_out": 400}, prices) == pytest.approx(0.034)


@pytest.mark.phase4b
def test_credits_meta_through_call_is_costed(db, monkeypatch):
    rules = load_rules()
    rules["prices_inr"]["crustdata_credit"] = 0.25
    monkeypatch.setattr(sources, "load_rules", lambda: rules)
    store.submit("j1", "t", {})
    row, resp = sources.call("j1", ["crustdata"], {"q": "x"}, {"crustdata": lambda q: ({"hits": [q]}, {"credits": 8})})
    assert resp == {"hits": ["x"]} and row["cost_inr"] == 2.0 and row["status"] == "done"


@pytest.mark.phase4b
def test_limit_is_not_applied_to_a_stored_response(db, monkeypatch):
    run_case(2)  # GST response now stored
    rules = load_rules()
    rules["limits"]["cost_cap_inr"] = 0
    monkeypatch.setattr(sources, "load_rules", lambda: rules)
    monkeypatch.setitem(sources.PRICE_KEYS, "fixture", "cashfree_gstin")
    job_id = worker.submit({**load_case(2)["request"], "client_id": "other-client"})
    worker.run_job(job_id)
    c2 = next(c for c in store.latest_verdict(job_id)["checks"] if c["check"] == "c2")
    assert c2["result"] == "pass", "a resumed or repeated job is never blocked on a call already paid for"


@pytest.mark.phase4b
def test_offline_is_reset_when_redecide_fails(db, monkeypatch):
    job_id, _ = run_case(4)
    monkeypatch.setitem(worker.STEP_FNS, "checks", lambda *_: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        worker.redecide(job_id)
    assert sources.OFFLINE is False


@pytest.mark.phase4b
def test_redecide_endpoint_and_history(db):
    client = TestClient(app_module.app)  # no context manager: the lifespan (worker subprocess) is not started
    job_id = worker.submit(load_case(4)["request"])
    assert client.post(f"/jobs/{job_id}/redecide").status_code == 409, "queued jobs cannot be redecided"
    worker.run_job(job_id)
    r = client.post(f"/jobs/{job_id}/redecide")
    assert r.status_code == 200 and r.json()["verdict"] == "Reject"
    j = client.get(f"/jobs/{job_id}").json()
    assert [h["verdict"] for h in j["history"]] == ["Reject", "Reject"]
    assert j["verdict"]["card"]["approver"]["cost_inr"] == j["cost_inr"]
    assert client.post("/jobs/nope/redecide").status_code == 404


@pytest.mark.phase4b
def test_offline_call_uses_stored_responses_only(db, monkeypatch):
    store.submit("j1", "t", {})
    fetched = []
    fns = {"p": lambda q: fetched.append(q) or {"ok": q}}
    sources.call("j1", ["p"], {"q": "stored"}, fns)
    monkeypatch.setattr(sources, "OFFLINE", True)
    assert sources.call("j1", ["p"], {"q": "stored"}, fns)[1] == {"ok": "stored"}
    with pytest.raises(sources.SourceUnavailable):
        sources.call("j1", ["p"], {"q": "new"}, fns)
    assert fetched == ["stored"], "offline mode never goes out"


@pytest.mark.phase4b
def test_redecide_blocks_a_check_that_tries_to_fetch(db, monkeypatch):
    """Phase 2/4-A checks may call sources (e.g. Crustdata inside c3); redecide must still make no outside call."""
    job_id, _ = run_case(4)
    fetched = []
    real_checks = worker.STEP_FNS["checks"]

    def checks_that_fetch(job, done):
        try:
            sources.call(job["job_id"], ["p"], {"q": "new"}, {"p": lambda q: fetched.append(q) or {}})
        except sources.SourceUnavailable:
            pass
        return real_checks(job, done)
    monkeypatch.setitem(worker.STEP_FNS, "checks", checks_that_fetch)
    worker.redecide(job_id)
    assert fetched == []
