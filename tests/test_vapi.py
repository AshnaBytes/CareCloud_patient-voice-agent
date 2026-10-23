import json

from tests.conftest import VALID

H = {"x-vapi-secret": "test-secret"}


def call(client, name, args, call_id="call_1"):
    payload = {"message": {"type": "tool-calls", "call": {"id": call_id},
                           "toolCallList": [{"id": "tc_1", "type": "function",
                                             "function": {"name": name, "arguments": args}}]}}
    r = client.post("/vapi/webhook", json=payload, headers=H)
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["toolCallId"] == "tc_1"
    return json.loads(res["result"])


def test_requires_secret(client):
    assert client.post("/vapi/webhook", json={}).status_code == 401


def test_register_then_duplicate_then_update(client):
    assert call(client, "check_existing_patient", {"phone_number": "2125550147"})["exists"] is False
    ok = call(client, "register_patient", {**VALID, "email": "", "address_line_2": "null"})
    assert ok["success"] and ok["first_name"] == "Jane"

    again = call(client, "check_existing_patient", {"phone_number": "212-555-0147"})
    assert again["exists"] and again["last_name"] == "Doe"

    dup = call(client, "register_patient", VALID, call_id="call_2")
    assert dup["success"] is False and dup["error_type"] == "duplicate"

    upd = call(client, "update_patient", {"patient_id": ok["patient_id"], "city": "Houston"}, call_id="call_2")
    assert upd["success"]
    assert client.get(f"/patients/{ok['patient_id']}").json()["data"]["city"] == "Houston"


def test_validation_feedback_names_fields(client):
    res = call(client, "register_patient", {**VALID, "phone_number": "555", "date_of_birth": "13/45/2020"})
    assert res["success"] is False and res["error_type"] == "validation"
    assert {e["field"] for e in res["errors"]} == {"phone_number", "date_of_birth"}


def test_arguments_as_json_string_and_unknown_tool(client):
    r = client.post("/vapi/webhook", headers=H, json={"message": {"type": "tool-calls", "toolCallList": [
        {"id": "a", "function": {"name": "check_existing_patient", "arguments": "{\"phone_number\": \"2125550000\"}"}},
        {"id": "b", "function": {"name": "nope", "arguments": {}}}]}})
    results = r.json()["results"]
    assert json.loads(results[0]["result"])["exists"] is False
    assert json.loads(results[1]["result"])["success"] is False


def test_end_of_call_report_links_transcript(client):
    pid = call(client, "register_patient", VALID, call_id="call_9")["patient_id"]
    r = client.post("/vapi/webhook", headers=H, json={"message": {
        "type": "end-of-call-report", "call": {"id": "call_9"}, "endedReason": "assistant-ended-call",
        "artifact": {"transcript": "AI: Hi..."}, "analysis": {"summary": "Registered Jane."}}})
    assert r.status_code == 200
    calls = client.get(f"/patients/{pid}/calls").json()["data"]
    assert calls[0]["summary"] == "Registered Jane." and calls[0]["transcript"].startswith("AI")
