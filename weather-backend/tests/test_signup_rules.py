import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.routers import auth


class SignupRulesTests(unittest.TestCase):
    @patch.dict(os.environ, {"AUTH_SECRET_KEY": "test-key-for-signup-validation-only"})
    @patch.object(auth, "get_conn")
    def test_invalid_names_are_rejected_before_database_access(self, connect):
        client = TestClient(app)
        for username in ("1234", "weather", "한글25", "rain-25", "a1", "a" * 20 + "1"):
            with self.subTest(username=username):
                self.assertEqual(client.get("/api/auth/username-availability", params={"username": username}).status_code, 400)
                self.assertEqual(client.post("/api/signup", json={
                    "username": username, "name": "테스트", "password": "sample-password",
                }).status_code, 400)
        connect.assert_not_called()

    @patch.object(auth, "get_conn")
    def test_availability_accepts_mixed_username(self, connect):
        cursor = connect.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        result = TestClient(app).get("/api/auth/username-availability", params={"username": "Weather25"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {"username": "Weather25", "available": True})
