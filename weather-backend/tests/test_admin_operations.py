import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.routers import notices


class AdminOperationsTests(unittest.TestCase):
    def test_admin_reads_and_candidate_write_require_authentication(self):
        client = TestClient(app)
        for path in ("/api/admin/activity", "/api/admin/mountain-candidates", "/api/admin/notices"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 401)
        response = client.put("/api/admin/mountain-candidates", json={"name": "북한산", "kind": "course"})
        self.assertEqual(response.status_code, 401)

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
