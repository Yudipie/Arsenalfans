"""Intake: masking, format checks (c1) and the Haiku parse of pasted text into the onboarding record."""
import hashlib
import re

from pydantic import ValidationError

from cc import sources
from cc.core import Bank, CheckResult, OnboardingRecord, ParsedText, Request, Vendor, load_rules

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
# 4-4-4, optional space or hyphen; never part of a longer number (a spaced account number).
_AADHAAR_RE = re.compile(r"(?<![+\d])(?<!\d[\s-])[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}(?![\s-]?\d)")
_ACCOUNT_RE = re.compile(r"(?i)\b(a/?c|acct|account)(\s*(?:no\.?|number|num)?\s*[:#-]?\s*)(\d(?:[ -]?\d){8,17})(?!\d)")
_LONG_NUMBER_RE = re.compile(r"\b\d(?:[ -]?\d){8,17}\b")  # 9-18 digits, maybe grouped (a spaced account number)
_PHONE_RE = re.compile(r"(?<![\d+])(?:\+?91[\s-]?|0)?\(?[6-9](?:\)?[\s-]?\d){9}(?!\d)")  # Indian mobile, any grouping
# "पता" takes no \b: its last character (a vowel sign) is not a word character, so \b after it never matches.
_ADDRESS_KEYWORD_RE = re.compile(r"(?im)^(\s*(?:(?:address|addr|pata)\b|पता)\s*[:#-]?)(.*)$")
_PIN_LINE_RE = re.compile(r"(?m)^.*\b\d{6}\b.*$")
_ID_TOKEN_RE = re.compile(r"\b(?:\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]{3}|[A-Z]{4}0[A-Z0-9]{6}|[A-Z]{5}\d{4}[A-Z])\b")
# Imperatives aimed at the model or the review, not ordinary words: "please approve our onboarding" or
# "ignore the earlier GSTIN" are honest request lines and must reach the parse.
_INSTRUCTION_RE = re.compile(
    r"(?i)^\s*(system|assistant|user)\s*:"
    r"|\b(ignore|disregard|forget)\b.{0,30}\b(instructions?|rules?|prompt|checks?|review)\b"
    r"|\bapprove\b.{0,40}\b(without|regardless|anyway|automatically|skip(ping)?)\b"
    r"|\b(skip|bypass|override)\b.{0,30}\b(review|checks?|verification|rules?|instructions?|verdict)\b")

# Verhoeff tables (Aadhaar's check digit).
_V_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
        [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
        [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
        [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_V_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
        [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
        [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def verhoeff_ok(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(re.sub(r"\D", "", number))):
        c = _V_D[c][_V_P[i % 8][int(ch)]]
    return c == 0
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


def _last4(digits: str) -> str:
    return re.sub(r"\D", "", digits)[-4:]


def mask_text(text: str, fields: dict) -> str:
    """Mask before anything is stored or shown to the model. Order matters: account (it has a prefix), then
    phone (so a +91/91-prefixed number is a phone, not an Aadhaar), then Aadhaar, then the rest.
    Address text is masked; GSTIN, IFSC and PAN tokens on that line are kept."""
    def address(masked: str) -> str:
        return " ".join(["[address]", *_ID_TOKEN_RE.findall(masked)])

    def aadhaar(m):
        return "[aadhaar removed]" if verhoeff_ok(m.group()) else f"[number ****{_last4(m.group())}]"

    def account(m):
        digits = re.sub(r"\D", "", m.group(3))
        if not fields.get("account_hash"):
            fields["account_hash"], fields["account_last4"] = account_hash(digits), digits[-4:]
        return f"{m.group(1)}{m.group(2)}[account ****{digits[-4:]}]"

    text = _ACCOUNT_RE.sub(account, text)
    text = _PHONE_RE.sub("[phone]", text)
    text = _AADHAAR_RE.sub(aadhaar, text)
    text = _LONG_NUMBER_RE.sub(lambda m: f"[number ****{_last4(m.group())}]", text)
    text = _ADDRESS_KEYWORD_RE.sub(lambda m: f"{m.group(1)} {address(m.group(2))}", text)
    text = _PIN_LINE_RE.sub(lambda m: address(m.group()), text)
    for d in fields.get("directors") or []:
        if d.get("name"):
            text = re.sub(re.escape(d["name"]), "[personal]", text, flags=re.I)
    return text


def flag_lines(text: str | None) -> list[str]:
    """Lines that read like instructions. Found by code, shown to the approver, and they change nothing."""
    return [ln.strip() for ln in (text or "").splitlines() if _INSTRUCTION_RE.search(ln)]


def scrub_request(req: dict) -> dict:
    """Runs before anything is stored. The raw account number becomes hash + last 4; pasted text is masked
    (Aadhaar dropped; account, phone, long numbers, address lines and director names masked)."""
    req = {**req, "fields": dict(req.get("fields") or {})}
    f = req["fields"]
    if f.get("account_number"):
        digits = re.sub(r"\D", "", f.pop("account_number"))
        f["account_hash"], f["account_last4"] = account_hash(digits), digits[-4:]
    if req.get("text"):
        req["text"] = mask_text(req["text"], f)
    return req


def parse_text(job_id: str, text: str) -> tuple[ParsedText | None, list[str]]:
    """Haiku parse of masked text, flagged lines removed. Schema-checked; one retry; None means could not parse.
    No source, no claim: an identifier not written word for word in the text is dropped."""
    flagged = set(flag_lines(text))
    clean = "\n".join(ln for ln in text.splitlines() if ln.strip() not in flagged)
    providers = load_rules()["providers"]["parse"]
    for attempt in (1, 2):
        try:
            _row, resp = sources.call(job_id, providers, {"text": clean, "attempt": attempt}, sources.PARSE_PROVIDERS)
            parsed = ParsedText.model_validate_json(resp["text"])
        except (sources.SourceUnavailable, ValidationError, KeyError, TypeError):
            continue
        notes = []
        for k in ("gstin", "pan", "ifsc"):
            v = getattr(parsed, k)
            if v and v not in clean:
                notes.append(f"parsed {k} '{v}' is not written in the text; dropped")
                setattr(parsed, k, None)
        return parsed, notes
    return None, []


def record_from_request(job_id: str, req: Request) -> OnboardingRecord:
    """Form fields map straight to the record. Pasted text is parsed by Haiku; form fields win over it."""
    f = req.fields.model_copy()
    notes, flagged, failed = [], flag_lines(req.text), False
    language = load_rules()["default_language"]
    if req.text:
        parsed, notes = parse_text(job_id, req.text)
        if parsed is None:
            failed = True
        else:
            for k in ("name", "gstin", "pan", "state", "city", "ifsc", "payee_name", "contact_email"):
                if not getattr(f, k) and getattr(parsed, k):
                    setattr(f, k, getattr(parsed, k))
            notes, language = parsed.parse_notes + notes, parsed.language
    pan = f.pan or (pan_from_gstin(f.gstin) if f.gstin else None)
    record = OnboardingRecord(
        job_id=job_id,
        vendor=Vendor(name_as_written=f.name, gstin=f.gstin, pan=f.pan, state=f.state, city=f.city,
                      pan_holder_type=PAN_HOLDER_TYPES.get(pan[3]) if pan and len(pan) > 3 else None,
                      directors=["[personal]" for _ in f.directors]),
        bank=Bank(ifsc=f.ifsc, account_hash=f.account_hash, account_last4=f.account_last4, payee_name=f.payee_name),
        language=language, parse_notes=notes, flagged_lines=flagged, parse_failed=failed,
    )
    record.missing = [k for k in ("name", "gstin", "ifsc", "account_hash") if not getattr(f, k)]
    return record


def check_formats(record: OnboardingRecord) -> tuple[CheckResult, list[str]]:
    """c1: code only, no paid call. Returns the result and parse notes. Nothing is ever corrected."""
    v, notes = record.vendor, []
    g = v.gstin

    def cv(rule_id: str, gap: str) -> tuple[CheckResult, list[str]]:
        return CheckResult(check="c1", result="cannot_verify", rule_id=rule_id, gap=gap), notes

    if record.parse_failed:
        if not g and not v.name_as_written:
            return cv("parse_failed", "could not read the pasted text")
        notes.append("could not read the pasted text; checked the form fields only")
    if not g and not v.name_as_written:
        return cv("name_and_gstin_missing", "carrier name or GSTIN")
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
