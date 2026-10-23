import os
import tempfile

# Must be set before the app is imported.
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["SEED_DEMO_DATA"] = "false"
os.environ["VAPI_SECRET"] = "test-secret"

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c


VALID = {
    "first_name": "Jane", "last_name": "Doe", "date_of_birth": "01/02/1990", "sex": "Female",
    "phone_number": "(212) 555-0147", "address_line_1": "12 Main St", "city": "Austin",
    "state": "tx", "zip_code": "78701",
}
