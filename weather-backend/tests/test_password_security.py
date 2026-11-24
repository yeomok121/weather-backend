import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import bcrypt
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core import auth_attempts
from app.core.auth import validate_new_password
from app.routers import auth


class PasswordSecurityTests(unittest.TestCase):
    def test_failed_verification_limit_is_shared_between_member_routes(self):
        app = FastAPI()
        app.include_router(auth.router)
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = None
        with (
            TestClient(app) as client,
            patch.object(auth_attempts, "_buckets", {}),
            patch.object(auth_attempts, "monotonic") as clock,
            patch.object(auth, "get_conn", return_value=connection) as connect,
            patch.object(auth, "_ensure_admin_tables"),
        ):
            clock.return_value = 1000
            login = {"username": "demo1", "password": "wrong"}
            change = {"username": "demo1", "current_password": "wrong", "new_password": "Cloudy trails near dawn!"}
            for _ in range(4):
                self.assertEqual(client.post("/api/login", json=login).status_code, 401)
            self.assertEqual(client.post("/api/password/reset", json=change).status_code, 401)
            blocked = client.post("/api/login", json=login)
            self.assertEqual(blocked.status_code, 429)
            self.assertEqual(blocked.headers["Retry-After"], "30")
            self.assertEqual(connect.call_count, 5)

            clock.return_value += 30
            self.assertEqual(client.post("/api/login", json=login).status_code, 401)
            self.assertEqual(client.post("/api/password/reset", json=change).headers["Retry-After"], "60")

    def test_ip_limit_spans_accounts_and_ignores_non_auth_failures(self):
        peer = SimpleNamespace(client=SimpleNamespace(host="test-peer"))
        with patch.object(auth_attempts, "_buckets", {}):
            for index in range(30):
                with self.assertRaises(HTTPException):
                    with auth_attempts.credential_attempt(peer, f"demo-{index}"):
                        raise HTTPException(503)
                with self.assertRaises(HTTPException) as failure:
                    with auth_attempts.credential_attempt(peer, f"demo-{index}"):
                        raise HTTPException(401)
                self.assertEqual(failure.exception.status_code, 401)
            with self.assertRaises(HTTPException) as blocked:
                with auth_attempts.credential_attempt(peer, "new-account"):
                    self.fail("a blocked attempt entered the verification block")
            self.assertEqual(blocked.exception.status_code, 429)

    def test_password_rule_applies_to_new_secrets(self):
        for password in ("short!", "12345678", "alphabet", "123456７８", "abc!abc!", "demo1 with a long phrase!", "비" * 25 + "!"):
            with self.subTest(password=password), self.assertRaises(HTTPException):
                validate_new_password("demo1", password)
        validate_new_password("demo1", "Cloud!78")
        validate_new_password("demo1", "Cloudy trails near dawn!")

    def test_legacy_password_still_signs_in(self):
        app = FastAPI()
        app.include_router(auth.router)
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {
            "id": 1, "username": "legacy1", "name": "Test", "suspended": 0, "token_version": 0,
            "password_hash": bcrypt.hashpw(b"1234", bcrypt.gensalt(rounds=4)).decode(),
        }
        with (
            TestClient(app) as client,
            patch.object(auth_attempts, "_buckets", {}),
            patch.object(auth, "get_conn", return_value=connection),
            patch.object(auth, "_ensure_admin_tables"),
            patch.object(auth, "ensure_member_sessions"),
            patch.object(auth, "create_member_session", return_value="test-token"),
        ):
            response = client.post("/api/login", json={"username": "legacy1", "password": "1234"})
            cursor.fetchone.return_value["password_hash"] = bcrypt.hashpw(b"Cloud!78", bcrypt.gensalt(rounds=4)).decode()
            compliant = client.post("/api/login", json={"username": "legacy1", "password": "Cloud!78"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["access_token"], "test-token")
        self.assertIs(response.json()["password_needs_update"], True)
        self.assertIs(compliant.json()["password_needs_update"], False)

    def test_stale_password_hash_does_not_invalidate_sessions_or_commit(self):
        app = FastAPI()
        app.include_router(auth.router)
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.rowcount = 0
        with (
            TestClient(app) as client,
            patch.object(auth_attempts, "_buckets", {}),
            patch.object(auth, "get_conn", return_value=connection),
            patch.object(auth, "_verify_user", return_value={"id": 1, "password_hash": "old-hash"}),
            patch("app.core.auth._hash_pw", return_value="new-hash"),
        ):
            response = client.post("/api/password/reset", json={
                "username": "demo1", "current_password": "old", "new_password": "Cloudy trails near dawn!",
            })
            self.assertEqual(response.status_code, 409)
            cursor.execute.assert_called_once()
            self.assertIn("AND BINARY password_hash = %s", cursor.execute.call_args.args[0])
            self.assertEqual(cursor.execute.call_args.args[1], ("new-hash", 1, "old-hash"))
            connection.commit.assert_not_called()

            cursor.rowcount = 1
            response = client.post("/api/password/reset", json={
                "username": "demo1", "current_password": "old", "new_password": "Cloudy trails near dawn!",
            })
            self.assertEqual(response.status_code, 200)
            self.assertIn("token_version=token_version+1", cursor.execute.call_args.args[0])
            connection.commit.assert_called_once()
