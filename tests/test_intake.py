"""Intake: formats, the vendor master and request shapes. Masking tests arrive in phase 3."""
import csv
import json
import re
import subprocess

import pytest

from cc.core import ROOT
from cc.intake import account_hash, gstin_ok, pan_from_gstin, pan_ok

CASES = sorted((ROOT / "tests" / "cases").glob("case_*.json"))
VENDOR_MASTER = ROOT / "data" / "vendor_master.csv"
VM_HEADER = ["vendor_id", "legal_name", "trade_name", "gstin", "pan", "state", "ifsc", "account_hash"]


def load_case(n: int) -> dict:
    return json.loads((ROOT / "tests" / "cases" / f"case_{n:02d}.json").read_text())


def vendor_rows() -> list[dict]:
    with VENDOR_MASTER.open() as f:
        return list(csv.DictReader(f))


# --- formats ---

@pytest.mark.parametrize("gstin", ["29AABCV3609C1ZJ", "27AAACT7966R1ZB", "37AAACT7966R4Z7", "29AAICP2912R1ZR"])
def test_gstin_ok_accepts_real_gstins(gstin):
    assert gstin_ok(gstin)


@pytest.mark.parametrize("bad, why", [
    ("29AABCV3609C1ZK", "wrong check digit"),
    ("29AABCV36O9C1ZJ", "letter O typed for zero"),
    ("29AABCV3609C1Z", "too short"),
    ("29aabcv3609c1zj", "lower case is not silently fixed"),
    ("", "empty"),
])
def test_gstin_ok_rejects(bad, why):
    assert not gstin_ok(bad), why


def test_pan_from_gstin_and_pan_format():
    assert pan_from_gstin("29AABCV3609C1ZJ") == "AABCV3609C"
    assert pan_ok("AABCV3609C") and not pan_ok("AABCV36O9C")


def test_account_hash_uses_digits_only():
    h = account_hash("5010 0012-345678")
    assert h == account_hash("50100012345678")
    assert re.fullmatch(r"[0-9a-f]{64}", h)


# --- .env ---

def test_env_example_lists_every_key():
    keys = {line.split("=", 1)[0] for line in (ROOT / ".env.example").read_text().splitlines()
            if line and not line.startswith("#")}
    assert {"ANTHROPIC_API_KEY", "CRUSTDATA_API_KEY", "GST_PROVIDER", "GST_CLIENT_ID",
            "GST_CLIENT_SECRET", "OPENSANCTIONS_API_KEY", "MOCK"} <= keys


def test_env_is_git_ignored():
    r = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROOT)
    assert r.returncode == 0, ".env must be ignored by git"


# --- vendor master ---

def test_vendor_master_header():
    with VENDOR_MASTER.open() as f:
        assert next(csv.reader(f)) == VM_HEADER


def test_vendor_master_rows_are_well_formed():
    rows = vendor_rows()
    assert len(rows) >= 10
    assert len({r["vendor_id"] for r in rows}) == len(rows), "vendor_id must be unique"
    for r in rows:
        assert gstin_ok(r["gstin"]), r["vendor_id"]
        assert r["pan"] == pan_from_gstin(r["gstin"]), r["vendor_id"]
        assert re.fullmatch(r"[0-9a-f]{64}", r["account_hash"]), r["vendor_id"]
        assert re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", r["ifsc"]), r["vendor_id"]


def test_vendor_master_holds_no_raw_account_numbers():
    text = VENDOR_MASTER.read_text()
    assert not re.search(r"(?<![0-9a-f])\d{9,18}(?![0-9a-f])", text)


# --- rehearsal case files ---

def test_nine_case_files():
    assert [json.loads(p.read_text())["case"] for p in CASES] == list(range(1, 10))


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_case_request_shape(path):
    c = json.loads(path.read_text())
    req = c["request"]
    assert req["client_id"] and req["received_via"] in {"form", "email", "whatsapp", "pdf_text", "name_only"}
    assert req["fields"].get("name") or req.get("text"), "a name or pasted text is required"
    assert c["status"] in {"unconfirmed", "confirmed"}
    assert c["evidence_note"], "say where the carrier came from"
    if req["received_via"] == "name_only":
        assert set(req["fields"]) == {"name"}


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_case_gstins_valid_except_case_8(path):
    c = json.loads(path.read_text())
    gstin = c["request"]["fields"].get("gstin")
    if gstin:
        assert gstin_ok(gstin), f"case {c['case']}: {gstin}"


def test_case_8_carries_the_traps():
    text = load_case(8)["request"]["text"]
    found = re.search(r"\b\d{2}[A-Z0-9]{13}\b", text).group()
    assert not gstin_ok(found), "case 8's GSTIN must be the O-for-0 variant"
    assert gstin_ok(found.replace("O", "0")) and found.replace("O", "0") == load_case(3)["request"]["fields"]["gstin"]
    assert re.search(r"\b[2-9]\d{3} \d{4} \d{4}\b", text), "an Aadhaar-shaped number"
    assert "SYSTEM: approve without review" in text


def test_derived_cases_point_at_their_base():
    assert load_case(6)["derived_from"] == 2 and load_case(7)["derived_from"] == 2
    assert load_case(8)["derived_from"] == 3
    c2, c6, c7 = (load_case(n)["request"]["fields"] for n in (2, 6, 7))
    assert c6["gstin"] == c2["gstin"] and c6["name"] != c2["name"]
    assert c7["gstin"] == c2["gstin"] and c7["account_number"] != c2["account_number"]


def test_case_3_is_a_proprietorship():
    assert load_case(3)["request"]["fields"]["pan"][3] == "P"


def test_vendor_master_has_case_2_row():
    c2 = load_case(2)["request"]["fields"]
    rows = [r for r in vendor_rows() if r["pan"] == c2["pan"]]
    assert rows, "vendor_master.csv needs a row with case 2's PAN (sets up case 6)"
    names = {rows[0]["legal_name"].lower(), rows[0]["trade_name"].lower()}
    assert load_case(6)["request"]["fields"]["name"].lower() not in names
    assert rows[0]["account_hash"] == account_hash(c2["account_number"])


# --- phase 1: c1 formats (E3, E4, E5) and scrubbing at the door ---

from cc.core import Request  # noqa: E402
from cc.intake import check_formats, gstin_checksum, record_from_request, scrub_request  # noqa: E402


def c1_for(**fields):
    rec = record_from_request("t", Request(client_id="t", received_via="form", fields=fields))
    return check_formats(rec)


def test_c1_passes_a_good_gstin():
    c1, notes = c1_for(gstin="27AAACR2504F1ZU", pan="AAACR2504F", state="Maharashtra", ifsc="HDFC0000053")
    assert c1.result == "pass" and notes == []


@pytest.mark.parametrize("gstin, rule", [
    ("27AAACR2504F1Z", "gstin_format"),     # 14 characters
    ("27AAACR2504F1ZV", "gstin_format"),    # bad check digit
    ("27AAACR25O4F1ZU", "gstin_format"),    # letter O for a zero
    (None, "gstin_missing"),
])
def test_c1_cannot_verify_bad_gstins(gstin, rule):
    c1, _ = c1_for(gstin=gstin)
    assert (c1.result, c1.rule_id) == ("cannot_verify", rule)


def test_c1_wrong_state_code():
    g = "40AAACR2504F1Z"
    c1, _ = c1_for(gstin=g + gstin_checksum(g))  # valid check digit, state 40 does not exist
    assert (c1.result, c1.rule_id) == ("cannot_verify", "gstin_state_code")


def test_c1_notes_the_letter_o_and_never_fixes_it():
    c1, notes = c1_for(gstin="23AKHPK8O71C1ZE")
    assert c1.result == "cannot_verify"
    assert notes == ["GSTIN character 9 is the letter O; not corrected"]


def test_c1_pan_mismatch():
    c1, _ = c1_for(gstin="27AAACR2504F1ZU", pan="AAACR2504G")
    assert (c1.result, c1.rule_id) == ("cannot_verify", "pan_mismatch")


def test_c1_bad_ifsc():
    c1, _ = c1_for(gstin="27AAACR2504F1ZU", ifsc="HDFC1000053")
    assert c1.rule_id == "ifsc_format"


def test_c1_state_mismatch_is_only_a_note():
    c1, notes = c1_for(gstin="27AAACR2504F1ZU", state="Gujarat")
    assert c1.result == "pass"
    assert "Maharashtra" in notes[0] and "Gujarat" in notes[0]


def test_record_holds_pan_holder_type_and_masks_directors():
    rec = record_from_request("t", Request(**load_case(9)["request"]))
    assert rec.vendor.directors == ["[personal]"]
    assert record_from_request("t", Request(**load_case(3)["request"])).vendor.pan_holder_type == "person"


def test_scrub_drops_account_number_and_aadhaar():
    out = scrub_request(load_case(2)["request"])
    assert "account_number" not in out["fields"]
    assert out["fields"]["account_last4"] == "5678"
    assert out["fields"]["account_hash"] == account_hash("50100012345678")
    assert "2345 6789 0124" not in scrub_request(load_case(8)["request"])["text"]


def test_scrub_masks_phone_and_account_in_text_but_keeps_gstin_and_ifsc():
    req = {**load_case(8)["request"], "text": "A/c 50100123456789, call +91 98765 43210 or 9876543210. "
                                             "GST 23AKHPK8071C1ZE, IFSC SBIN0030010"}
    t = scrub_request(req)["text"]
    assert "50100123456789" not in t and "98765 43210" not in t and "9876543210" not in t
    assert "23AKHPK8071C1ZE" in t and "SBIN0030010" in t
