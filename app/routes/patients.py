"""REST API. Every response uses the envelope {"data": ..., "error": ...}."""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import services
from app.database import get_db
from app.errors import AppError
from app.schemas import PatientCreate, PatientOut, PatientUpdate

log = logging.getLogger("api")
router = APIRouter(tags=["patients"])


def ok(data, status: int = 200) -> JSONResponse:
    return JSONResponse({"data": data, "error": None}, status_code=status)


def serialize(p) -> dict:
    return PatientOut.model_validate(p).model_dump(mode="json")


def _check_id(patient_id: str) -> str:
    try:
        return str(uuid.UUID(patient_id))
    except ValueError:
        raise AppError(400, "invalid_id", "patient_id must be a valid UUID")


def _not_found():
    return AppError(404, "not_found", "Patient not found")


def _duplicate(e: services.DuplicatePhoneError):
    details = {"existing_patient_id": e.existing.patient_id} if e.existing else None
    return AppError(409, "duplicate_phone", str(e), details)


@router.get("/patients")
def list_patients(last_name: Optional[str] = None, date_of_birth: Optional[str] = None,
                  phone_number: Optional[str] = None, db: Session = Depends(get_db)):
    try:
        patients = services.list_patients(db, last_name, date_of_birth, phone_number)
    except ValueError as e:
        raise AppError(400, "invalid_query", str(e))
    return ok([serialize(p) for p in patients])


@router.get("/patients/{patient_id}")
def get_patient(patient_id: str, db: Session = Depends(get_db)):
    try:
        return ok(serialize(services.get_patient(db, _check_id(patient_id))))
    except services.NotFoundError:
        raise _not_found()


@router.post("/patients")
def create_patient(payload: PatientCreate, db: Session = Depends(get_db)):
    try:
        p = services.create_patient(db, payload)
    except services.DuplicatePhoneError as e:
        raise _duplicate(e)
    log.info("Patient created via API: %s", p.patient_id)
    return ok(serialize(p), 201)


@router.put("/patients/{patient_id}")
def update_patient(patient_id: str, payload: PatientUpdate, db: Session = Depends(get_db)):
    try:
        return ok(serialize(services.update_patient(db, _check_id(patient_id), payload)))
    except services.NotFoundError:
        raise _not_found()
    except services.DuplicatePhoneError as e:
        raise _duplicate(e)


@router.delete("/patients/{patient_id}")
def delete_patient(patient_id: str, db: Session = Depends(get_db)):
    try:
        return ok(serialize(services.soft_delete_patient(db, _check_id(patient_id))))
    except services.NotFoundError:
        raise _not_found()


@router.get("/patients/{patient_id}/calls")
def patient_calls(patient_id: str, db: Session = Depends(get_db)):
    """Bonus: call transcripts/summaries linked to a patient."""
    pid = _check_id(patient_id)
    try:
        services.get_patient(db, pid)
    except services.NotFoundError:
        raise _not_found()
    return ok([{"vapi_call_id": c.vapi_call_id, "ended_reason": c.ended_reason,
                "summary": c.summary, "transcript": c.transcript}
               for c in services.list_calls_for_patient(db, pid)])
