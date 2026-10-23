from tests.conftest import VALID


def test_create_and_get(client):
    r = client.post("/patients", json=VALID)
    assert r.status_code == 201
    body = r.json()
    assert body["error"] is None
    d = body["data"]
    assert d["phone_number"] == "2125550147" and d["state"] == "TX" and d["date_of_birth"] == "01/02/1990"
    assert d["preferred_language"] == "English" and d["created_at"].endswith("Z")
    g = client.get(f"/patients/{d['patient_id']}")
    assert g.status_code == 200 and g.json()["data"]["last_name"] == "Doe"


def test_validation_errors_422(client):
    bad = {**VALID, "phone_number": "123", "date_of_birth": "01/01/2999", "state": "ZZ", "zip_code": "1234"}
    r = client.post("/patients", json=bad)
    assert r.status_code == 422
    fields = {e["field"] for e in r.json()["error"]["details"]}
    assert {"phone_number", "date_of_birth", "state", "zip_code"} <= fields


def test_missing_required_422(client):
    r = client.post("/patients", json={"first_name": "A"})
    assert r.status_code == 422


def test_malformed_json_400(client):
    r = client.post("/patients", content="{oops", headers={"content-type": "application/json"})
    assert r.status_code == 400


def test_invalid_uuid_400_and_404(client):
    assert client.get("/patients/not-a-uuid").status_code == 400
    assert client.get("/patients/6f1c2f5e-0000-4000-8000-000000000000").status_code == 404


def test_filters(client):
    client.post("/patients", json=VALID)
    assert len(client.get("/patients?last_name=doe").json()["data"]) == 1
    assert len(client.get("/patients?last_name=smith").json()["data"]) == 0
    assert len(client.get("/patients?date_of_birth=01/02/1990").json()["data"]) == 1
    assert len(client.get("/patients?phone_number=212-555-0147").json()["data"]) == 1
    assert client.get("/patients?date_of_birth=nope").status_code == 400


def test_partial_update(client):
    pid = client.post("/patients", json=VALID).json()["data"]["patient_id"]
    r = client.put(f"/patients/{pid}", json={"city": "Dallas", "email": "Jane@Example.com"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["city"] == "Dallas" and d["email"] == "jane@example.com" and d["first_name"] == "Jane"
    assert client.put(f"/patients/{pid}", json={"state": "XX"}).status_code == 422
    assert client.put(f"/patients/{pid}", json={"first_name": None}).status_code == 422


def test_soft_delete(client):
    pid = client.post("/patients", json=VALID).json()["data"]["patient_id"]
    r = client.delete(f"/patients/{pid}")
    assert r.status_code == 200 and r.json()["data"]["deleted_at"]
    assert client.get(f"/patients/{pid}").status_code == 404
    assert client.get("/patients").json()["data"] == []
    # phone is free again after soft delete
    assert client.post("/patients", json=VALID).status_code == 201


def test_duplicate_phone_409(client):
    client.post("/patients", json=VALID)
    r = client.post("/patients", json={**VALID, "first_name": "Other"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_phone"


def test_sanitisation(client):
    r = client.post("/patients", json={**VALID, "address_line_1": "<script>alert(1)</script> 5 Elm"})
    assert "<" not in r.json()["data"]["address_line_1"]
    assert client.post("/patients", json={**VALID, "first_name": "Rob3rt", "phone_number": "2125550199"}).status_code == 422
    assert client.post("/patients", json={**VALID, "last_name": "O'Brien-Smith", "phone_number": "2125550198"}).status_code == 201
