"""Vapi <-> backend adapter.

Vapi POSTs JSON to /vapi/webhook. We handle two message types:
  * tool-calls          -> run the requested tool, reply {"results":[{"toolCallId","result"}]}
  * end-of-call-report  -> store transcript/summary; log if the call ended without a registration
Everything else (status-update, transcript, ...) is acknowledged and ignored.

Tools return *structured, speakable* results. On validation failure the result lists the exact
fields + reasons so the LLM re-prompts for just those fields. On a DB failure it returns
error_type="system" so the agent apologises instead of going silent.
"""
import json
import logging
import secrets
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import config, services
from app.database import get_db
from app.schemas import PatientCreate, PatientUpdate, format_errors, normalize_phone

log = logging.getLogger("vapi")
router = APIRouter(tags=["vapi"])

_NULLISH = {"null", "none", "n/a", "na", "no", "nil", "skip"}
_FIELDS = set(PatientCreate.model_fields)


def _verify_secret(request: Request) -> bool:
    if not config.VAPI_SECRET:
        return True
    supplied = request.headers.get("x-vapi-secret") or request.headers.get("authorization", "").removeprefix("Bearer ")
    return secrets.compare_digest(supplied or "", config.VAPI_SECRET)


def _clean_args(args: Dict[str, Any]) -> Dict[str, Any]:
    """LLMs sometimes send "" / "null" for skipped optional fields, or extra keys. Drop them."""
    out = {}
    for k, v in args.items():
        if k not in _FIELDS:
            continue
        if v is None or (isinstance(v, str) and (not v.strip() or v.strip().lower() in _NULLISH)):
            continue
        out[k] = v
    return out


def _validation_failure(exc: ValidationError) -> dict:
    return {"success": False, "error_type": "validation", "errors": format_errors(exc.errors()),
            "message": "Some fields are invalid. Ask the caller again for ONLY those fields."}


def _system_failure(exc: Exception, tool: str) -> dict:
    log.exception("Tool %s failed", tool)
    return {"success": False, "error_type": "system",
            "message": "The registration system is temporarily unavailable. Apologise and offer to retry once."}


def tool_check_existing_patient(db: Session, args: dict, call_id: str) -> dict:
    try:
        phone = normalize_phone(args.get("phone_number", ""))
    except ValueError as e:
        return {"success": False, "error_type": "validation",
                "errors": [{"field": "phone_number", "message": str(e)}]}
    p = services.find_by_phone(db, phone)
    if not p:
        return {"success": True, "exists": False}
    # Only expose what the agent needs to say out loud.
    return {"success": True, "exists": True, "patient_id": p.patient_id,
            "first_name": p.first_name, "last_name": p.last_name}


def tool_register_patient(db: Session, args: dict, call_id: str) -> dict:
    try:
        data = PatientCreate.model_validate(_clean_args(args))
    except ValidationError as e:
        return _validation_failure(e)
    try:
        p = services.create_patient(db, data)
    except services.DuplicatePhoneError as e:
        ex = e.existing
        return {"success": False, "error_type": "duplicate",
                "message": "A record with this phone number already exists. Offer to update it instead.",
                **({"patient_id": ex.patient_id, "first_name": ex.first_name, "last_name": ex.last_name} if ex else {})}
    # Observability requirement: log the final collected payload.
    log.info("FINAL PATIENT PAYLOAD (call=%s): %s", call_id,
             json.dumps(data.model_dump(mode="json"), default=str))
    if call_id:
        services.upsert_call_log(db, call_id, patient_id=p.patient_id)
    return {"success": True, "patient_id": p.patient_id, "first_name": p.first_name}


def tool_update_patient(db: Session, args: dict, call_id: str) -> dict:
    patient_id = args.get("patient_id")
    if not patient_id:
        return {"success": False, "error_type": "validation",
                "errors": [{"field": "patient_id", "message": "patient_id is required; call check_existing_patient first"}]}
    fields = _clean_args({k: v for k, v in args.items() if k != "patient_id"})
    try:
        data = PatientUpdate.model_validate(fields)
    except ValidationError as e:
        return _validation_failure(e)
    try:
        p = services.update_patient(db, patient_id, data)
    except services.NotFoundError:
        return {"success": False, "error_type": "not_found", "message": "No matching patient record."}
    except services.DuplicatePhoneError:
        return {"success": False, "error_type": "duplicate",
                "message": "That phone number belongs to a different record."}
    log.info("PATIENT UPDATED (call=%s) fields=%s", call_id, sorted(fields))
    if call_id:
        services.upsert_call_log(db, call_id, patient_id=p.patient_id)
    return {"success": True, "patient_id": p.patient_id, "first_name": p.first_name}


TOOLS = {
    "check_existing_patient": tool_check_existing_patient,
    "register_patient": tool_register_patient,
    "update_patient": tool_update_patient,
}


def _extract_tool_calls(message: dict) -> List[dict]:
    """Vapi has shipped a few payload shapes; accept all of them."""
    calls = message.get("toolCallList") or message.get("toolCalls")
    if not calls:
        calls = [t.get("toolCall", t) for t in message.get("toolWithToolCallList", [])]
    parsed = []
    for c in calls or []:
        fn = c.get("function") or {}
        args = fn.get("arguments", c.get("arguments", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except json.JSONDecodeError:
                args = {}
        parsed.append({"id": c.get("id"), "name": fn.get("name", c.get("name")), "args": args or {}})
    return parsed


@router.post("/vapi/webhook")
async def vapi_webhook(request: Request, db: Session = Depends(get_db)):
    if not _verify_secret(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    message = body.get("message", body)
    mtype = message.get("type")
    call_id = (message.get("call") or {}).get("id")

    if mtype == "tool-calls":
        results = []
        for tc in _extract_tool_calls(message):
            handler = TOOLS.get(tc["name"])
            if not handler:
                result = {"success": False, "error_type": "system", "message": f"Unknown tool {tc['name']}"}
            else:
                try:
                    result = handler(db, tc["args"], call_id)
                except Exception as exc:  # never leave the caller in silence
                    db.rollback()
                    result = _system_failure(exc, tc["name"])
            # Vapi requires `result` to be a single-line string.
            results.append({"toolCallId": tc["id"], "result": json.dumps(result)})
        return {"results": results}

    if mtype == "end-of-call-report" and call_id:
        artifact = message.get("artifact") or {}
        analysis = message.get("analysis") or {}
        ended = message.get("endedReason")
        try:
            logrow = services.upsert_call_log(
                db, call_id, ended_reason=ended,
                transcript=artifact.get("transcript") or message.get("transcript"),
                summary=analysis.get("summary") or message.get("summary"))
            if not logrow.patient_id:
                log.warning("Call %s ended WITHOUT a saved registration (endedReason=%s)", call_id, ended)
            else:
                log.info("Call %s finished, patient=%s, endedReason=%s", call_id, logrow.patient_id, ended)
        except Exception:
            db.rollback()
            log.exception("Failed to store end-of-call report")
        return {"ok": True}

    return {"ok": True}
