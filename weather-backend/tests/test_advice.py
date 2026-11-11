import unittest
from unittest.mock import Mock, patch

from app import advice


class GeminiAdviceTests(unittest.TestCase):
    def setUp(self):
        advice.AI_CACHE.clear()

    @patch.object(advice, 'GEMINI_API_KEY', 'test-key')
    @patch.object(advice.genai, 'Client')
    def test_new_sdk_response_is_parsed(self, client_factory):
        client = client_factory.return_value
        client.models.generate_content.return_value = Mock(text='한줄: 우산 준비\n상세: 비 예보를 확인하세요.')
        result = advice.get_ai_contents({'temp': 12}, [], {})
        self.assertEqual(result, {'one_line': '우산 준비', 'briefing': '비 예보를 확인하세요.'})
        client_factory.assert_called_once_with(api_key='test-key')
        self.assertTrue(client.models.generate_content.called)

    @patch.object(advice, 'GEMINI_API_KEY', 'test-key')
    @patch.object(advice.genai, 'Client', side_effect=RuntimeError('unavailable'))
    def test_sdk_failure_returns_fallback(self, _client):
        result = advice.get_ai_contents({'temp': 12}, [], {})
        self.assertTrue(result['one_line'])
        self.assertTrue(result['briefing'])
