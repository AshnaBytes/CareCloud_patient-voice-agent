"""Data/service layer. The REST routes AND the Vapi tool handlers both call these
functions, so business rules live in exactly one place."""
from datetime import date
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CallLog, Patient, utcnow
from app.schemas import PatientCreate, PatientUpdate, normalize_phone, parse_dob


class NotFoundError(Exception):
    pass


class DuplicatePhoneError(Exception):
    def __init__(self, existing: Optional[Patient] = None):
        self.existing = existing
        super().__init__("A patient with this phone number already exists")


def _active():
    return Patient.deleted_at.is_(None)


def find_by_phone(db: Session, phone: str) -> Optional[Patient]:
    return db.scalars(select(Patient).where(Patient.phone_number == normalize_phone(phone), _active())).first()


def get_patient(db: Session, patient_id: str) -> Patient:
    p = db.scalars(select(Patient).where(Patient.patient_id == patient_id, _active())).first()
    if not p:
        raise NotFoundError(patient_id)
    return p


def list_patients(db: Session, last_name: Optional[str] = None, date_of_birth: Optional[str] = None,
                  phone_number: Optional[str] = None) -> List[Patient]:
    """Raises ValueError for malformed filters (route maps it to HTTP 400)."""
    q = select(Patient).where(_active())
    if last_name:
        q = q.where(func.lower(Patient.last_name) == last_name.strip().lower())
    if date_of_birth:
        q = q.where(Patient.date_of_birth == parse_dob(date_of_birth))
    if phone_number:
        q = q.where(Patient.phone_number == normalize_phone(phone_number))
    return list(db.scalars(q.order_by(Patient.created_at.desc())))


def create_patient(db: Session, data: PatientCreate) -> Patient:
    existing = find_by_phone(db, data.phone_number)
    if existing:
        raise DuplicatePhoneError(existing)
    p = Patient(**data.model_dump())
    db.add(p)
    try:
        db.commit()
    except IntegrityError:  # race: two writers, same phone
        db.rollback()
        raise DuplicatePhoneError(find_by_phone(db, data.phone_number))
    return p


def update_patient(db: Session, patient_id: str, data: PatientUpdate) -> Patient:
    p = get_patient(db, patient_id)
    changes = data.model_dump(exclude_unset=True)
    new_phone = changes.get("phone_number")
    if new_phone and new_phone != p.phone_number:
        clash = find_by_phone(db, new_phone)
        if clash and clash.patient_id != p.patient_id:
            raise DuplicatePhoneError(clash)
    for k, v in changes.items():
        setattr(p, k, v)
    p.updated_at = utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicatePhoneError()
    return p


def soft_delete_patient(db: Session, patient_id: str) -> Patient:
    p = get_patient(db, patient_id)
    p.deleted_at = utcnow()
    db.commit()
    return p


def upsert_call_log(db: Session, vapi_call_id: str, **fields) -> CallLog:
    log = db.scalars(select(CallLog).where(CallLog.vapi_call_id == vapi_call_id)).first()
    if not log:
        log = CallLog(vapi_call_id=vapi_call_id)
        db.add(log)
    for k, v in fields.items():
        if v is not None:
            setattr(log, k, v)
    db.commit()
    return log


def list_calls_for_patient(db: Session, patient_id: str) -> List[CallLog]:
    return list(db.scalars(select(CallLog).where(CallLog.patient_id == patient_id).order_by(CallLog.created_at.desc())))


def seed_demo_data(db: Session) -> None:
    if db.scalars(select(Patient).limit(1)).first():
        return
    db.add(Patient(first_name="Demo", last_name="Patient", date_of_birth=date(1985, 6, 15), sex="Other",
                   phone_number="2125550100", email="demo.patient@example.com",
                   address_line_1="1 Demo Street", city="New York", state="NY", zip_code="10001",
                   preferred_language="English"))
    db.commit()
