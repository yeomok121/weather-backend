import unittest
from datetime import date, datetime
from uuid import uuid4
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from fastapi import HTTPException

from app.core.auth import _protect_last_superadmin
from app.main import app
from app.routers import admin, notices


class AdminOperationsTests(unittest.TestCase):
    def test_admin_reads_and_candidate_write_require_authentication(self):
        client = TestClient(app)
        for path in ("/api/admin/activity", "/api/admin/mountain-candidates", "/api/admin/notices", "/api/admin/usage"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 401)
        response = client.put("/api/admin/mountain-candidates", json={"name": "북한산", "kind": "course"})
        self.assertEqual(response.status_code, 401)

    def test_public_usage_rejects_non_service_pages_and_unknown_mountain_names(self):
        client = TestClient(app)
        ids = {"event_id": str(uuid4()), "browser_id": str(uuid4())}
        self.assertEqual(client.post("/api/page-views", json={**ids, "page": "admin"}).status_code, 422)
        self.assertEqual(client.post("/api/page-views", json={**ids, "page": "mountain", "mountain": "등록되지 않은 산"}).status_code, 422)
        self.assertEqual(client.post("/api/page-views", json={**ids, "page": "weather", "mountain": "북한산"}).status_code, 422)

    @patch.object(admin, "require_admin", return_value={"name": "operator", "role": "superadmin"})
    def test_even_superadmin_cannot_set_a_members_password(self, auth):
        response = TestClient(app).put("/api/admin/users/1", json={
            "username": "member1", "name": "회원", "password": "example!99",
        })
        self.assertEqual(response.status_code, 403)
        auth.assert_called_once_with(None, None, "members")

    @patch.object(admin, "require_admin", return_value={"name": "operator", "role": "member_manager"})
    def test_member_manager_cannot_change_roles(self, auth):
        response = TestClient(app).patch("/api/admin/users/1/state", json={"role": "member_manager", "reason": "테스트"})
        self.assertEqual(response.status_code, 403)
        auth.assert_called_once_with(None, None, "members")

    def test_last_active_superadmin_is_protected(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"user_id": 1}]
        with self.assertRaises(HTTPException) as error:
            _protect_last_superadmin(cursor, 1)
        self.assertEqual(error.exception.status_code, 409)
        _protect_last_superadmin(cursor, 2)
        cursor.fetchall.return_value += [{"user_id": 2}]
        _protect_last_superadmin(cursor, 1)

    @patch.object(admin, "_usage_window", return_value=(date(2026, 11, 19), datetime(2026, 11, 18, 15)))
    @patch.object(admin, "get_conn")
    @patch.object(admin, "require_admin")
    def test_usage_distinguishes_uncollected_days_from_zero_views(self, auth, connect, window):
        cursor = connect.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [
            {"started_at": datetime(2026, 11, 23, 15)},
            {"visitors": 1, "views": 2},
            {"total": 5, "not_found": 1}, {"total": 6, "not_found": 2}, {"total": 7, "not_found": 0},
        ]
        cursor.fetchall.side_effect = [
            [{"day": date(2026, 11, 24), "visitors": 1, "views": 2}],
            [{"page": "mountain", "views": 2}],
            [{"mountain": "북한산", "views": 2}],
        ]
        result = admin.admin_usage(7, None, "test")
        auth.assert_called_once_with(None, "test", "system")
        self.assertEqual(result["daily"][4]["views"], None)
        self.assertEqual(result["daily"][5]["views"], 2)
        self.assertEqual(result["daily"][6]["views"], 0)
        self.assertEqual(result["totals"], {"visitors": 1, "views": 2, "searches": 11, "no_results": 3, "fishing_selections": 7})

    @patch.object(notices, "_ensure_admin_tables")
    @patch.object(notices, "_ensure_notices_table")
    @patch.object(notices, "get_conn")
    @patch.object(notices, "require_admin", return_value={"name": "operator"})
    def test_draft_creation_records_publication_state_and_actor(self, auth, connect, notice_schema, admin_schema):
        connection = MagicMock()
        connect.return_value = connection
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.lastrowid = 41
        notices.admin_create_notice(notices.NoticeRequest(title="점검 예정", date="2026.11.18", state="draft"))
        writes = [args for _, args, _ in cursor.execute.mock_calls]
        self.assertTrue(any("INSERT INTO notice_publication" in sql and params == (41, "draft") for sql, params in writes))
        self.assertTrue(any("INSERT INTO admin_audit_log" in sql and params == ("operator", "notice_create", 41, "공지 #41") for sql, params in writes))
        connection.commit.assert_called_once()
