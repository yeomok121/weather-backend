import unittest
from unittest.mock import Mock, patch
from app import weather


class MountainCatalogTests(unittest.TestCase):
    def test_catalog_has_50_unique_valid_mountains_and_search(self):
        self.assertEqual(len(weather.MOUNTAINS_DB), 50)
        with patch.object(weather, 'refresh_mountain_catalog'):
            for name, item in weather.MOUNTAINS_DB.items():
                with self.subTest(name=name):
                    self.assertTrue(33 <= item['lat'] <= 39)
                    self.assertTrue(124 <= item['lon'] <= 132)
                    self.assertGreater(item['elevation'], 0)
                    self.assertEqual(weather.search_mountain(name)['name'], name)

    def test_catalog_survives_rejected_provider_key(self):
        response = Mock(status_code=200)
        response.json.return_value = {'response': {'header': {'resultCode': '30'}}}
        with patch.dict(weather.os.environ, {'MOUNTAIN_LIST_API_KEY': 'test-only-key'}), patch.object(weather, '_mountain_list_checked_at', float('-inf')), patch.object(weather.requests, 'get', return_value=response):
            weather.refresh_mountain_catalog()
            self.assertEqual(weather._mountain_catalog_diagnostics['status'], 'provider_error')
            self.assertEqual(weather._mountain_catalog_diagnostics['provider_code'], '30')
            self.assertEqual(len(weather.MOUNTAINS_DB), 50)
            self.assertNotIn('test-only-key', str(weather._mountain_catalog_diagnostics))

    def test_added_mountain_summary_preserves_missing_weekly_fields(self):
        name = next(name for name, item in weather.MOUNTAINS_DB.items() if item.get('course_available') is False)
        current = {'temp': 15, 'wind_speed': 2, 'humidity': 70, 'sunrise': '06:00', 'sunset': '18:00', 'description': '맑음', 'icon': '01d'}
        hourly = [{'temp': 13, 'wind_speed': 2, 'humidity': 70, 'icon': '01d', 'pop': 0, 'time': '12시', 'description': '맑음'}]
        weekly = [{'temp_max': 15, 'temp_min': 5, 'wind_speed': None, 'humidity': None, 'icon': '01d', 'pop': 0}]
        with patch.object(weather, 'refresh_mountain_catalog'), patch.object(weather, 'get_current_weather', return_value=current), patch.object(weather, 'get_hourly_forecast', return_value=hourly), patch.object(weather, 'get_air_quality', return_value={'pm25': None}), patch.object(weather, 'get_weekly_forecast', return_value=weekly):
            result = weather.get_mountain_weather(name)
        self.assertEqual(result['mountain_name'], name)
        self.assertFalse(result['mountain_info']['course_available'])
        self.assertEqual(len(result['hourly']), 1)
        self.assertIsNone(result['weekly'][0]['wind_speed'])
        self.assertIsNone(result['weekly'][0]['humidity'])


if __name__ == '__main__':
    unittest.main()
