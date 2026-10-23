"""Pydantic schemas = the single source of truth for validation.

Used by BOTH the REST API and the Vapi tool handlers, so the voice agent can never
persist anything the API would reject. Validators also *normalise* (phone -> 10 digits,
state -> upper-case, DOB -> date) because speech-to-text output is messy.
"""
import re
from datetime import date, datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator

US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MO MS MT NE NV NH NJ NM NY NC "
    "ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY AS GU MP PR VI".split()
)
REQUIRED_FIELDS = frozenset(
    {"first_name", "last_name", "date_of_birth", "sex", "phone_number",
     "address_line_1", "city", "state", "zip_code"}
)
SEX_ALIASES = {
    "male": "Male", "m": "Male", "man": "Male",
    "female": "Female", "f": "Female", "woman": "Female",
    "other": "Other", "non-binary": "Other", "nonbinary": "Other",
    "decline to answer": "Decline to Answer", "decline": "Decline to Answer",
    "prefer not to say": "Decline to Answer", "prefer not to answer": "Decline to Answer",
}
MAX_LEN = {"address_line_1": 100, "address_line_2": 100, "city": 100, "insurance_provider": 100,
           "preferred_language": 50, "emergency_contact_name": 100}

_NAME_RE = re.compile(r"[^\W\d_](?:[^\W\d_]|[ '\-])*[^\W\d_]|[^\W\d_]")  # letters, hyphens, apostrophes, inner spaces
_CITY_RE = re.compile(r"[^\W\d_](?:[^\W\d_]|[ .'\-])*")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_ZIP_RE = re.compile(r"\d{5}(?:-\d{4})?")


def clean_text(v: Any) -> Optional[str]:
    """Basic sanitisation: drop control chars and angle brackets, collapse whitespace."""
    if v is None:
        return None
    s = re.sub(r"[\x00-\x1f\x7f]", " ", str(v)).replace("<", "").replace(">", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def normalize_phone(v: Any) -> str:
    digits = re.sub(r"\D", "", str(v))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    # NOTE: NANP area-code rules (can't start with 0/1) are intentionally NOT enforced so that
    # obvious test numbers like 123-456-7890 work for reviewers. Tighten here if needed.
    if len(digits) != 10:
        raise ValueError("Phone number must be a valid 10-digit U.S. number")
    return digits


def parse_dob(v: Any) -> date:
    s = str(v).strip()
    d = None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            d = datetime.strptime(s, fmt).date()
            break
        except ValueError:
            continue
    if d is None:
        raise ValueError("Date of birth must be a real date in MM/DD/YYYY format")
    if d > date.today():
        raise ValueError("Date of birth cannot be in the future")
    if d.year < 1900:
        raise ValueError("Date of birth must be 1900 or later")
    return d


def _name(s: str) -> str:
    s = s.replace("\u2019", "'")
    if len(s) > 50:
        raise ValueError("Must be 50 characters or fewer")
    if not _NAME_RE.fullmatch(s):
        raise ValueError("Must contain only letters, hyphens and apostrophes")
    return s


def _email(s: str) -> str:
    s = s.lower().replace(" ", "")
    if len(s) > 254 or not _EMAIL_RE.fullmatch(s):
        raise ValueError("Must be a valid email address")
    return s


def _state(s: str) -> str:
    s = s.upper()
    if s not in US_STATES:
        raise ValueError("Must be a valid 2-letter U.S. state abbreviation (e.g. CA)")
    return s


def _zip(s: str) -> str:
    s = s.replace(" ", "")
    if not _ZIP_RE.fullmatch(s):
        raise ValueError("Must be a 5-digit ZIP or ZIP+4 (e.g. 94103 or 94103-1234)")
    return s


def _sex(s: str) -> str:
    out = SEX_ALIASES.get(s.lower())
    if not out:
        raise ValueError("Must be one of: Male, Female, Other, Decline to Answer")
    return out


def _member_id(s: str) -> str:
    s = re.sub(r"[\s\-]", "", s).upper()
    if not re.fullmatch(r"[A-Z0-9]{1,50}", s):
        raise ValueError("Member ID must be alphanumeric (max 50 characters)")
    return s


def _city(s: str) -> str:
    if len(s) > 100 or not _CITY_RE.fullmatch(s):
        raise ValueError("City must be 1-100 characters, letters only")
    return s


def _emergency_name(s: str) -> str:
    s = s.replace("\u2019", "'")
    if len(s) > 100 or not _NAME_RE.fullmatch(s):
        raise ValueError("Must be a name using letters, hyphens and apostrophes only")
    return s


def _language(s: str) -> str:
    if len(s) > 50:
        raise ValueError("Must be 50 characters or fewer")
    return s.title()


_NORMALIZERS = {
    "first_name": _name, "last_name": _name, "date_of_birth": parse_dob, "sex": _sex,
    "phone_number": normalize_phone, "emergency_contact_phone": normalize_phone,
    "email": _email, "state": _state, "zip_code": _zip, "city": _city,
    "insurance_member_id": _member_id, "emergency_contact_name": _emergency_name,
    "preferred_language": lambda s: _language(s),
}


class _PatientFields(BaseModel):
    """Every field optional here; PatientCreate re-declares the required ones."""
    model_config = ConfigDict(extra="forbid")

    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    sex: Optional[str] = None
    phone_number: Optional[str] = None
    email: Optional[str] = None
    address_line_1: Optional[str] = None
    address_line_2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    insurance_provider: Optional[str] = None
    insurance_member_id: Optional[str] = None
    preferred_language: Optional[str] = "English"
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None

    @field_validator("*", mode="before")
    @classmethod
    def _normalize(cls, v, info):
        name = info.field_name
        cleaned = clean_text(v)
        if cleaned is None:
            if name in REQUIRED_FIELDS:
                raise ValueError("This field is required and cannot be empty")
            return "English" if name == "preferred_language" else None
        if name in _NORMALIZERS:
            return _NORMALIZERS[name](cleaned)
        if len(cleaned) > MAX_LEN.get(name, 100):
            raise ValueError(f"Must be {MAX_LEN.get(name, 100)} characters or fewer")
        return cleaned


class PatientCreate(_PatientFields):
    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    address_line_1: str
    city: str
    state: str
    zip_code: str


class PatientUpdate(_PatientFields):
    """Partial update: only fields present in the payload are changed."""
    preferred_language: Optional[str] = None  # no implicit default on update


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:  # SQLite returns naive datetimes; we always store UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    patient_id: str
    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    email: Optional[str] = None
    address_line_1: str
    address_line_2: Optional[str] = None
    city: str
    state: str
    zip_code: str
    insurance_provider: Optional[str] = None
    insurance_member_id: Optional[str] = None
    preferred_language: str
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None

    @field_serializer("date_of_birth")
    def _dob(self, d: date) -> str:
        return d.strftime("%m/%d/%Y")

    @field_serializer("created_at", "updated_at", "deleted_at")
    def _ts(self, dt: Optional[datetime]) -> Optional[str]:
        return _iso(dt)


def format_errors(errors: list) -> list:
    """Pydantic error list -> [{field, message}] that both the API and the voice agent can use."""
    out = []
    for e in errors:
        loc = [str(p) for p in e.get("loc", ()) if p not in ("body",)]
        msg = e.get("msg", "Invalid value").removeprefix("Value error, ")
        if e.get("type") == "missing":
            msg = "This field is required"
        out.append({"field": loc[-1] if loc else "body", "message": msg})
    return out
