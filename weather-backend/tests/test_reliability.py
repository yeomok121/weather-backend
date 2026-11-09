import unittest
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from pymysql.err import OperationalError
from app import weather
from app.main import app
from app.health import database_state


class WeatherDataQualityTests(unittest.TestCase):
    def response(self, data, status=200):
        response = Mock(status_code=status)
        response.json.return_value = data
        return response

    @patch.object(weather, 'get_yesterday_temp', return_value=None)
    @patch.object(weather, 'logged_get')
    def test_total_upstream_failure_never_becomes_twenty_degrees(self, get, yesterday):
        get.side_effect = [self.response({}), self.response({}, 503)]
        with self.assertRaises(RuntimeError):
            weather.get_current_weather(37.5, 126.7)

    @patch.object(weather, 'get_yesterday_temp', return_value=None)
    @patch.object(weather, 'logged_get')
    def test_real_zero_from_fallback_and_missing_fields_are_preserved(self, get, yesterday):
        get.side_effect = [self.response({}), self.response({'main': {'temp': 0}, 'weather': [{'description': '흐림', 'icon': '04d'}]})]
        result = weather.get_current_weather(37.5, 126.7)
        self.assertEqual(result['temp'], 0)
        self.assertIsNone(result['humidity'])
        self.assertIsNone(result['wind_speed'])
        self.assertIsNone(result['sunset'])
        self.assertIn('대체 자료', result['weather_sources']['temperature'])

    @patch.object(weather, 'logged_get')
    def test_hourly_missing_temperature_is_not_fabricated(self, get):
        get.return_value = self.response({'response': {'body': {'items': {'item': [
            {'fcstDate': '20991109', 'fcstTime': '1200', 'category': 'WSD', 'fcstValue': '2.5'},
        ]}}}})
        self.assertEqual(weather.get_hourly_forecast(37.5, 126.7), [])


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_liveness_survives_database_failure_but_dependency_check_detects_it(self):
        with patch.dict(database_state, {'status': 'unavailable', 'checked_at': None}):
            self.assertEqual(self.client.get('/api/health/live').status_code, 200)
            response = self.client.get('/api/health')
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()['database']['status'], 'unavailable')
        with patch.dict(database_state, {'status': 'available', 'checked_at': None}):
            self.assertEqual(self.client.get('/api/health').status_code, 200)

    def test_password_change_requires_proof_of_current_password(self):
        response = self.client.post('/api/password/reset', json={'username': 'other_user', 'name': 'Known Public Name', 'new_password': 'new-password'})
        self.assertEqual(response.status_code, 422)

    @patch('app.main.get_conn', side_effect=OperationalError(2003, 'private database address must never be returned'))
    def test_member_database_failure_returns_actionable_503_without_private_details(self, connection):
        response = self.client.post('/api/login', json={'username': 'qa_probe', 'password': 'invalid'})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private database address', response.text)
        self.assertEqual(response.headers['retry-after'], '30')


if __name__ == '__main__':
    unittest.main()
