from uuid import uuid4

import pytest
import requests

from app.models.user import User


@pytest.fixture
def base_url(fastapi_server: str) -> str:
    return fastapi_server.rstrip("/")


def user_payload(**overrides) -> dict:
    suffix = uuid4().hex[:10]
    data = {
        "first_name": "Test",
        "last_name": "User",
        "email": f"user_{suffix}@example.com",
        "username": f"user_{suffix}",
        "password": "SecurePass123!",
        "confirm_password": "SecurePass123!",
    }
    data.update(overrides)
    return data


def register(base_url: str, payload: dict):
    return requests.post(f"{base_url}/users/register", json=payload)


def login(base_url: str, username: str, password: str):
    return requests.post(
        f"{base_url}/users/login",
        json={"username": username, "password": password},
    )


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------
def test_register_stores_user_with_hashed_password(base_url, db_session):
    payload = user_payload()
    r = register(base_url, payload)
    assert r.status_code == 201, r.text

    body = r.json()
    assert body["username"] == payload["username"]
    assert body["is_active"] is True
    assert body["is_verified"] is False
    assert "password" not in body and "confirm_password" not in body

    stored = db_session.query(User).filter_by(username=payload["username"]).first()
    assert stored is not None
    assert stored.email == payload["email"]
    assert stored.password != payload["password"]
    assert stored.verify_password(payload["password"]) is True


@pytest.mark.parametrize("field", ["email", "username"])
def test_register_duplicate_rejected(base_url, field):
    first = user_payload()
    assert register(base_url, first).status_code == 201

    second = user_payload(**{field: first[field]})
    r = register(base_url, second)
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"]


@pytest.mark.parametrize("overrides", [
    {"password": "Sh0rt!", "confirm_password": "Sh0rt!"},                   # too short
    {"password": "alllowercase1!", "confirm_password": "alllowercase1!"},   # no uppercase
    {"password": "NODIGITSHERE!", "confirm_password": "NODIGITSHERE!"},     # no lowercase
    {"password": "NoDigitsHere!", "confirm_password": "NoDigitsHere!"},     # no digit
    {"password": "NoSpecial123", "confirm_password": "NoSpecial123"},       # no special char
    {"confirm_password": "Different123!"},                                  # mismatch
    {"email": "not-an-email"},
    {"username": "ab"},                                                     # too short
])
def test_register_invalid_data_returns_422(base_url, overrides):
    r = register(base_url, user_payload(**overrides))
    assert r.status_code == 422


def test_register_missing_field_returns_422(base_url):
    payload = user_payload()
    del payload["last_name"]
    assert register(base_url, payload).status_code == 422


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
def test_login_success_and_last_login_updated(base_url, db_session):
    payload = user_payload()
    register(base_url, payload)

    r = login(base_url, payload["username"], payload["password"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["token_type"] == "bearer"
    assert body["username"] == payload["username"]

    stored = db_session.query(User).filter_by(username=payload["username"]).first()
    assert stored.last_login is not None


def test_login_with_email(base_url):
    payload = user_payload()
    register(base_url, payload)
    assert login(base_url, payload["email"], payload["password"]).status_code == 200


def test_login_wrong_password_returns_401(base_url):
    payload = user_payload()
    register(base_url, payload)
    assert login(base_url, payload["username"], "WrongPass123!").status_code == 401


def test_login_unknown_user_returns_401(base_url):
    assert login(base_url, "nobody_here_123", "SecurePass123!").status_code == 401


def test_login_missing_password_returns_422(base_url):
    r = requests.post(f"{base_url}/users/login", json={"username": "someone"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Token protection
# ---------------------------------------------------------------------------
def test_protected_route_without_token_returns_401(base_url):
    assert requests.get(f"{base_url}/calculations").status_code == 401


def test_protected_route_with_bad_token_returns_401(base_url):
    headers = {"Authorization": "Bearer not.a.real.token"}
    assert requests.get(f"{base_url}/calculations", headers=headers).status_code == 401