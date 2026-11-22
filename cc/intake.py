"""Intake: format checks, masking and the Haiku parse (masking and parse arrive in phase 3)."""
import hashlib
import re

from cc.core import Bank, CheckResult, OnboardingRecord, Request, Vendor

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")


def gstin_checksum(first14: str) -> str:
    """The 15th GSTIN character, computed from the first 14."""
    total = 0
    for i, ch in enumerate(first14):
        product = _ALPHABET.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _ALPHABET[(36 - total % 36) % 36]


def gstin_ok(gstin: str) -> bool:
    """Format plus checksum. Never corrects the input (an O typed for a 0 fails)."""
    return bool(_GSTIN_RE.match(gstin or "")) and gstin_checksum(gstin[:14]) == gstin[14]


def pan_ok(pan: str) -> bool:
    return bool(_PAN_RE.match(pan or ""))


def pan_from_gstin(gstin: str) -> str:
    return gstin[2:12]


def account_hash(account_number: str) -> str:
    """sha256 of the digits only; the raw number is never stored."""
    return hashlib.sha256(re.sub(r"\D", "", account_number).encode()).hexdigest()


# --- phase 1: scrub at the door, request -> record, c1 formats ---

_IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_AADHAAR_RE = re.compile(r"\b[2-9]\d{3}\s?\d{4}\s?\d{4}\b")
_DIGIT_POSITIONS = (0, 1, 7, 8, 9, 10)  # where a GSTIN must have a digit
PAN_HOLDER_TYPES = {"C": "company", "P": "person", "H": "HUF", "F": "firm", "A": "AOP", "T": "trust",
                    "B": "BOI", "L": "local authority", "J": "artificial juridical person", "G": "government"}
GST_STATES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh", "05": "Uttarakhand",
    "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura",
    "17": "Meghalaya", "18": "Assam", "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat", "25": "Daman and Diu",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra", "28": "Andhra Pradesh",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
    "97": "Other Territory", "99": "Centre Jurisdiction",
}


def scrub_request(req: dict) -> dict:
    """Runs before anything is stored. The raw account number becomes hash + last 4; Aadhaar-shaped numbers
    are dropped from pasted text. Phase 3 adds phone, address and name masking."""
    req = {**req, "fields": dict(req.get("fields") or {})}
    f = req["fields"]
    if f.get("account_number"):
        digits = re.sub(r"\D", "", f.pop("account_number"))
        f["account_hash"], f["account_last4"] = account_hash(digits), digits[-4:]
    if req.get("text"):
        req["text"] = _AADHAAR_RE.sub("[aadhaar removed]", req["text"])
    return req


def record_from_request(job_id: str, req: Request) -> OnboardingRecord:
    """Form fields map straight to the record. The Haiku parse of pasted text arrives in phase 3."""
    f = req.fields
    notes = ["pasted text not parsed yet (phase 3)"] if req.text and not f.name else []
    pan = f.pan or (pan_from_gstin(f.gstin) if f.gstin else None)
    record = OnboardingRecord(
        job_id=job_id,
        vendor=Vendor(name_as_written=f.name, gstin=f.gstin, pan=f.pan, state=f.state, city=f.city,
                      pan_holder_type=PAN_HOLDER_TYPES.get(pan[3]) if pan and len(pan) > 3 else None,
                      directors=["[personal]" for _ in f.directors]),
        bank=Bank(ifsc=f.ifsc, account_hash=f.account_hash, account_last4=f.account_last4, payee_name=f.payee_name),
        parse_notes=notes,
    )
    record.missing = [k for k in ("name", "gstin", "ifsc", "account_hash") if not getattr(f, k)]
    return record


def check_formats(record: OnboardingRecord) -> tuple[CheckResult, list[str]]:
    """c1: code only, no paid call. Returns the result and parse notes. Nothing is ever corrected."""
    v, notes = record.vendor, []
    g = v.gstin

    def cv(rule_id: str, gap: str) -> tuple[CheckResult, list[str]]:
        return CheckResult(check="c1", result="cannot_verify", rule_id=rule_id, gap=gap), notes

    if not g:
        return cv("gstin_missing", "GSTIN")
    for i in _DIGIT_POSITIONS:
        if i < len(g) and g[i] == "O":
            notes.append(f"GSTIN character {i + 1} is the letter O; not corrected")
    if not gstin_ok(g):
        return cv("gstin_format", "GSTIN: wrong length, characters or check digit")
    if g[:2] not in GST_STATES:
        return cv("gstin_state_code", f"GSTIN state code {g[:2]} does not exist")
    if v.state and v.state.strip().lower() != GST_STATES[g[:2]].lower():
        notes.append(f"GSTIN state code {g[:2]} is {GST_STATES[g[:2]]}; the request says {v.state}")
    if v.pan and v.pan != pan_from_gstin(g):
        return cv("pan_mismatch", "PAN given differs from the PAN inside the GSTIN; ask which is right")
    if record.bank.ifsc and not _IFSC_RE.match(record.bank.ifsc):
        return cv("ifsc_format", "IFSC format")
    return CheckResult(check="c1", result="pass", rule_id="formats_ok"), notes
