"""Core: paths, .env and rules loading, the rules hash, and the pydantic contract models (overview §4.4)."""
import hashlib
import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
MODEL = "claude-haiku-5-5"

load_dotenv(ROOT / ".env")


def mock() -> bool:
    return os.getenv("MOCK", "0") == "1"


def load_rules() -> dict:
    """Read fresh every time; rules are data."""
    return yaml.safe_load((ROOT / "rules.yaml").read_text())


def rules_hash() -> str:
    return hashlib.sha256((ROOT / "rules.yaml").read_bytes()).hexdigest()


# --- contracts ---

class Director(BaseModel):
    name: str | None = None
    din: str | None = None


class Fields(BaseModel):
    """Request fields. account_number is accepted at the door and replaced by hash + last 4 before storage."""
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    gstin: str | None = None
    pan: str | None = None
    state: str | None = None
    city: str | None = None
    ifsc: str | None = None
    account_number: str | None = None
    account_hash: str | None = None
    account_last4: str | None = None
    payee_name: str | None = None
    directors: list[Director] = []
    contact_email: str | None = None


class Request(BaseModel):
    client_id: str
    received_via: Literal["form", "email", "whatsapp", "pdf_text", "name_only"]
    fields: Fields = Fields()
    text: str | None = None


class Vendor(BaseModel):
    name_as_written: str | None = None
    gstin: str | None = None
    pan: str | None = None
    pan_holder_type: str | None = None
    state: str | None = None
    city: str | None = None
    directors: list[str] = []


class Bank(BaseModel):
    ifsc: str | None = None
    account_hash: str | None = None
    account_last4: str | None = None
    payee_name: str | None = None


class OnboardingRecord(BaseModel):
    job_id: str
    vendor: Vendor
    bank: Bank
    language: Literal["en", "hi"] = "en"
    parse_notes: list[str] = []
    flagged_lines: list[str] = []
    missing: list[str] = []


class Evidence(BaseModel):
    fact_id: str
    value: object
    source: str
    field_path: str | None = None
    quote: str | None = None
    retrieved_at: str
    record_updated_at: str | None = None
    call_id: int
    mock: bool = False


class CheckResult(BaseModel):
    check: str
    result: Literal["pass", "fail", "refer", "cannot_verify"]
    rule_id: str
    facts: list[str] = []
    gap: str | None = None
    sources_tried: list[str] = []
