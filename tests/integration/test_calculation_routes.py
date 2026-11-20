from uuid import UUID, uuid4

import pytest
import requests

from app.models.calculation import Calculation


@pytest.fixture
def base_url(fastapi_server: str) -> str:
    return fastapi_server.rstrip("/")


def new_user_headers(base_url: str):
    """Register and log in a fresh user. Returns (auth headers, user_id)."""
    suffix = uuid4().hex[:10]
    payload = {
        "first_name": "Calc",
        "last_name": "Tester",
        "email": f"calc_{suffix}@example.com",
        "username": f"calc_{suffix}",
        "password": "SecurePass123!",
        "confirm_password": "SecurePass123!",
    }
    r = requests.post(f"{base_url}/users/register", json=payload)
    assert r.status_code == 201, r.text
    r = requests.post(
        f"{base_url}/users/login",
        json={"username": payload["username"], "password": payload["password"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user_id"]


def create_calc(base_url, headers, calc_type="addition", inputs=(10, 5), **extra):
    return requests.post(
        f"{base_url}/calculations",
        json={"type": calc_type, "inputs": list(inputs), **extra},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Add
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("calc_type, inputs, expected", [
    ("addition", [10, 5], 15),
    ("subtraction", [10, 5], 5),
    ("multiplication", [10, 5], 50),
    ("division", [10, 5], 2),
    ("addition", [1, 2, 3, 4], 10),
    ("ADDITION", [1, 2], 3),
])
def test_create_results(base_url, calc_type, inputs, expected):
    headers, _ = new_user_headers(base_url)
    r = create_calc(base_url, headers, calc_type, inputs)
    assert r.status_code == 201, r.text
    assert r.json()["result"] == expected


def test_create_saves_row_and_owner_comes_from_token(base_url, db_session):
    headers, user_id = new_user_headers(base_url)
    r = create_calc(base_url, headers, user_id=str(uuid4()))   # client value is ignored
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user_id"] == user_id

    row = db_session.query(Calculation).filter(Calculation.id == UUID(body["id"])).first()
    assert row is not None
    assert row.result == 15
    assert row.inputs == [10, 5]
    assert str(row.user_id) == user_id


@pytest.mark.parametrize("body", [
    {"type": "power", "inputs": [2, 3]},
    {"type": "addition", "inputs": [5]},
    {"type": "addition", "inputs": []},
    {"type": "addition", "inputs": "not-a-list"},
    {"type": "addition", "inputs": ["a", "b"]},
    {"type": "division", "inputs": [10, 0]},
    {"type": "division", "inputs": [10, 2, 0]},
    {"inputs": [1, 2]},
    {"type": "addition"},
])
def test_create_invalid_data_returns_422(base_url, body):
    headers, _ = new_user_headers(base_url)
    r = requests.post(f"{base_url}/calculations", json=body, headers=headers)
    assert r.status_code == 422


def test_create_requires_auth(base_url):
    r = requests.post(f"{base_url}/calculations", json={"type": "addition", "inputs": [1, 2]})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Browse / Read
# ---------------------------------------------------------------------------
def test_browse_returns_only_own_calculations(base_url):
    headers_a, _ = new_user_headers(base_url)
    headers_b, _ = new_user_headers(base_url)
    a1 = create_calc(base_url, headers_a).json()["id"]
    a2 = create_calc(base_url, headers_a, "multiplication", (2, 3)).json()["id"]
    b1 = create_calc(base_url, headers_b).json()["id"]

    ids_a = {c["id"] for c in requests.get(f"{base_url}/calculations", headers=headers_a).json()}
    ids_b = {c["id"] for c in requests.get(f"{base_url}/calculations", headers=headers_b).json()}
    assert ids_a == {a1, a2}
    assert ids_b == {b1}


def test_read_calculation(base_url):
    headers, _ = new_user_headers(base_url)
    created = create_calc(base_url, headers).json()
    r = requests.get(f"{base_url}/calculations/{created['id']}", headers=headers)
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]
    assert r.json()["result"] == 15


def test_read_invalid_uuid_returns_400(base_url):
    headers, _ = new_user_headers(base_url)
    assert requests.get(f"{base_url}/calculations/not-a-uuid", headers=headers).status_code == 400


def test_read_nonexistent_returns_404(base_url):
    headers, _ = new_user_headers(base_url)
    assert requests.get(f"{base_url}/calculations/{uuid4()}", headers=headers).status_code == 404


def test_cannot_read_another_users_calculation(base_url):
    owner, _ = new_user_headers(base_url)
    other, _ = new_user_headers(base_url)
    calc_id = create_calc(base_url, owner).json()["id"]
    assert requests.get(f"{base_url}/calculations/{calc_id}", headers=other).status_code == 404


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------
def test_update_recomputes_result(base_url, db_session):
    headers, _ = new_user_headers(base_url)
    calc_id = create_calc(base_url, headers, "multiplication", (3, 4)).json()["id"]

    r = requests.put(f"{base_url}/calculations/{calc_id}", json={"inputs": [5, 6]}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["result"] == 30

    row = db_session.query(Calculation).filter(Calculation.id == UUID(calc_id)).first()
    assert row.result == 30
    assert row.inputs == [5, 6]


def test_update_to_division_by_zero_rejected_and_unchanged(base_url):
    # Requires the try/except around get_result() in update_calculation (otherwise this is a 500).
    headers, _ = new_user_headers(base_url)
    calc_id = create_calc(base_url, headers, "division", (10, 5)).json()["id"]

    r = requests.put(f"{base_url}/calculations/{calc_id}", json={"inputs": [5, 0]}, headers=headers)
    assert r.status_code == 400

    current = requests.get(f"{base_url}/calculations/{calc_id}", headers=headers).json()
    assert current["inputs"] == [10, 5]
    assert current["result"] == 2


def test_update_with_too_few_inputs_returns_422(base_url):
    headers, _ = new_user_headers(base_url)
    calc_id = create_calc(base_url, headers).json()["id"]
    r = requests.put(f"{base_url}/calculations/{calc_id}", json={"inputs": [1]}, headers=headers)
    assert r.status_code == 422


def test_update_nonexistent_returns_404(base_url):
    headers, _ = new_user_headers(base_url)
    r = requests.put(f"{base_url}/calculations/{uuid4()}", json={"inputs": [1, 2]}, headers=headers)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
def test_delete_calculation(base_url, db_session):
    headers, _ = new_user_headers(base_url)
    calc_id = create_calc(base_url, headers).json()["id"]

    assert requests.delete(f"{base_url}/calculations/{calc_id}", headers=headers).status_code == 204
    assert requests.get(f"{base_url}/calculations/{calc_id}", headers=headers).status_code == 404

    db_session.expire_all()
    assert db_session.query(Calculation).filter(Calculation.id == UUID(calc_id)).first() is None


def test_delete_nonexistent_returns_404(base_url):
    headers, _ = new_user_headers(base_url)
    assert requests.delete(f"{base_url}/calculations/{uuid4()}", headers=headers).status_code == 404


def test_cannot_delete_another_users_calculation(base_url):
    owner, _ = new_user_headers(base_url)
    other, _ = new_user_headers(base_url)
    calc_id = create_calc(base_url, owner).json()["id"]

    assert requests.delete(f"{base_url}/calculations/{calc_id}", headers=other).status_code == 404
    assert requests.get(f"{base_url}/calculations/{calc_id}", headers=owner).status_code == 200