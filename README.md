# Voice AI Patient Registration

A caller dials a real U.S. phone number, talks to an LLM-driven intake agent ("Sam"), and a new patient record is
saved to a persistent database and exposed over a REST API. Calling back with the same phone number is recognised.

| | |
|---|---|
| **Phone number** | `+1 (424) 441 2013` |
| **API base URL** | `https://carecloud-patient-voice-agent.onrender.com` |
| **Interactive API docs** | `https://carecloud-patient-voice-agent.onrender.com/docs` |
| **Repo** | `https://github.com/AshnaBytes/CareCloud_patient-voice-agent` |

**Dashboard (bonus)** 'Did not deploy yet,but can be seen by running the command:
python -m http.server 5500 --directory static '

## Testing status (please read)

The system is deployed and working, but **it was not fully tested end to end** because of the time limit and limited Vapi credits.

- Done: deployed on Render, Vapi assistant + 3 tools + webhook connected, automated tests (`pytest`), and one live test call (screenshots in `Submission_Notes.docx`).
- First test call did not save: I gave a Pakistani address and the server correctly rejected it (US addresses/states/ZIPs only).
- Not tested live: update-existing-patient flow, mid-call hang-up, and repeated edge cases.
- Note: Render free tier may take ~50s to wake on the first request.


## Architecture

```
 Caller ──PSTN──▶ Vapi (telephony + STT + LLM + TTS)
                     │  tool-calls / end-of-call-report  (HTTPS webhook, shared-secret header)
                     ▼
              POST /vapi/webhook   ← app/vapi.py        (adapter: payload parsing, speakable results)
                     │
                     ▼
              app/services.py      ← business rules     (create / update / dedupe / soft-delete)
                     ▲                    │
   REST API ─────────┘                    ▼
   app/routes/patients.py           SQLAlchemy → SQLite (default) or PostgreSQL
   (same validation: app/schemas.py)
```

Separation of concerns:
- **Telephony / STT / TTS / dialogue** — Vapi (config in `vapi/`).
- **LLM logic** — system prompt + 3 tool definitions (`vapi/system_prompt.md`, `vapi/tools.json`).
- **Voice adapter** — `app/vapi.py` translates Vapi tool calls into service-layer calls.
- **Service layer** — `app/services.py`, used by *both* the REST API and the voice agent (one set of rules).
- **Validation** — `app/schemas.py` (Pydantic). The agent cannot save anything the API would reject.
- **Persistence** — `app/models.py` with DB-level constraints (CHECKs, partial unique index on active phone numbers).

## Tech stack & why
- **Vapi** — fastest path to a real phone number with good turn-taking/interruption handling; lets time go into prompt, tools and error handling instead of audio plumbing.
- **FastAPI + Pydantic v2** — typed validation, auto OpenAPI docs at `/docs`, small and quick.
- **SQLAlchemy + SQLite** — zero-setup, survives restarts when the file lives on a persistent disk/volume. Swap to Postgres by setting `DATABASE_URL` (no code change).
- **pytest** — API + webhook tests (`pytest -q`).

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # edit VAPI_SECRET
export $(grep -v '^#' .env | xargs)
uvicorn app.main:app --reload --port 8000
pytest -q
# expose to Vapi while developing:
ngrok http 8000
```
Docker: `docker build -t patient-api . && docker run -p 8000:8000 -v $(pwd)/data:/srv/data --env-file .env patient-api`

## Environment variables
| Var | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy URL (`sqlite:///./data/patients.db` or `postgresql://…`) | SQLite file in `./data` |
| `VAPI_SECRET` | Shared secret Vapi sends in `X-Vapi-Secret`; webhook returns 401 if wrong. Empty = check disabled | empty |
| `SEED_DEMO_DATA` | Insert one demo patient when table is empty | `true` |
| `LOG_LEVEL` | Python log level | `INFO` |

No API keys are in source code. Vapi/LLM keys live only in the Vapi dashboard.

## Vapi setup
1. Create the three Function tools from `vapi/tools.json` (Server URL `https://<host>/vapi/webhook`, secret = `VAPI_SECRET`).
2. Assistant → paste `vapi/system_prompt.md`, attach the tools, first message e.g. "Hi, thanks for calling. I'm Sam…".
3. Assistant → Advanced → Server Messages: enable `tool-calls` and `end-of-call-report`; set the Server URL + secret to the same webhook.
4. Enable the *End Call* tool (or "End call phrases") so the agent hangs up after "You're all set".
5. Attach the assistant to your Vapi phone number.

## REST API
Envelope on every response: `{ "data": ..., "error": null }` (errors: `{ "data": null, "error": {code, message, details} }`).

| Method | Path | Notes |
|---|---|---|
| GET | `/patients` | filters: `?last_name=` (case-insens.), `?date_of_birth=MM/DD/YYYY`, `?phone_number=` |
| GET | `/patients/{id}` | 400 bad UUID, 404 unknown/deleted |
| POST | `/patients` | 201; 422 field errors; 409 if an active patient already has that phone |
| PUT | `/patients/{id}` | partial update |
| DELETE | `/patients/{id}` | soft delete (`deleted_at`), record excluded from reads afterwards |
| GET | `/patients/{id}/calls` | bonus: linked call summary/transcript |
| GET | `/health`, `/dashboard`, `/docs` | |

Status codes: 200, 201, 400 (malformed JSON/UUID/filter), 404, 409, 422, 500. Phone numbers are normalised to 10 digits, state to upper-case, DOB returned as `MM/DD/YYYY`, timestamps as UTC ISO-8601 (`…Z`). Angle brackets/control characters are stripped from all text input.

## Prompt engineering (see `vapi/system_prompt.md`)
- **Persona + voice rules**: short turns, one question at a time, vary acknowledgements, no field names/jargon.
- **Flexible flow**: the model fills slots in any order and skips what the caller already volunteered (handles interruptions / out-of-order answers).
- **Normalisation table** (dates, phones, states, sex enum, email) so tool arguments are clean even from noisy STT.
- **Gated saving**: `register_patient` is only allowed after an explicit "yes" to the read-back.
- **Structured tool errors**: tools return `{success:false, errors:[{field,message}]}`, so the agent re-asks *only* for the broken field.
- **Today's date** injected via Liquid so future DOBs are rejected in-conversation *and* server-side (defence in depth).
- **Duplicate detection**: phone number checked as soon as it's captured.

## Edge cases & resilience
| Scenario | Behaviour |
|---|---|
| Invalid DOB / phone / state / ZIP | Server returns field-level errors → agent re-prompts for just that field |
| Corrections mid-call ("D-A-V-I-S not D-A-V-I-E-S") | Prompt: apply, read back once, continue; final read-back catches leftovers |
| Start over | Prompt instructs a clean restart; nothing is saved until confirmation |
| DB write fails | Tool returns `error_type:"system"`; agent apologises, retries once, then asks caller to call back. Webhook never raises to Vapi |
| Call drops mid-call | Nothing persisted before confirmation. `end-of-call-report` logs a WARNING "ended WITHOUT a saved registration" |
| Same phone calls again | `check_existing_patient` → offers update; DB partial-unique index + 409 prevent duplicates even under races |
| Webhook auth failure | 401 |

Logging: final collected payload is logged to stdout on every registration (`FINAL PATIENT PAYLOAD (call=…)`), plus call outcome on `end-of-call-report`.

## Known limitations / trade-offs
- Identity for "update my info" is only the phone number — a production system must verify (e.g. DOB) first.
- One active patient per phone number (families sharing a phone would need a different key).
- NANP area-code rules are intentionally relaxed so test numbers like `123-456-7890` are accepted.
- SQLite is single-writer; fine for a demo, use Postgres for real concurrency. Platform disks must be persistent (see deploy notes).
- No auth on the REST API and the dashboard is public — acceptable for a demo with fake data only; logs contain the demo PHI payload by design (requirement).
- State must be a USPS abbreviation after the agent's normalisation (server doesn't map "Texas" → "TX").
- Spanish support relies on the LLM prompt only (no separate Spanish voice/transcriber config).

## Next steps (if I had more time)
Test end to end, if the records are saving successfully into database, Verify identity before updates; appointment scheduling tool with mock slots.
