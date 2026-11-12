import unittest
from datetime import datetime
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import wind
from app.routers.wind import router

STATIONS = '''# STN_ID LON LAT STN_SP HT STN_KO STN_EN
108 126.9658 37.5714 0 86 서울 SEOUL
90 128.5647 38.2509 0 18 속초 SOKCHO
999 -9 -9 0 0 결측 MISSING
'''


class WindTests(unittest.TestCase):
    def setUp(self):
        wind._cache.clear()
        wind._retry_until = 0
        self.now = datetime(2026, 11, 12, 14, 10, tzinfo=wind.KST)
        self.stations = wind.parse_stations(STATIONS)

    def test_station_headers_join_and_missing_winds(self):
        observations = '''# YYMMDDHHMI STN WD WS GST_WD GST_WS
202611121400 108 36 4.5 36 7.0
202611121400 90 27 5.9 29 14.0
202611121400 999 9 2.0 9 3.0
202611121400 100 9 -9 9 -9
'''
        data = wind.parse_winds(observations, self.stations, self.now)
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]['name'], '속초')
        self.assertEqual(data[0]['direction'], 270)
        self.assertEqual(data[1]['direction'], 0)
        self.assertEqual(data[1]['speed'], 4.5)
        self.assertEqual(data[1]['lat'], 37.5714)

    def test_calm_zero_is_real_and_unknown_direction_is_not_north(self):
        data = wind.parse_winds('202611121400 108 0 0.0 -9 -9.0', self.stations, self.now)
        self.assertEqual(data[0]['speed'], 0)
        self.assertIsNone(data[0]['direction'])
        self.assertIsNone(data[0]['gust'])

    def test_old_future_missing_and_invalid_data_are_rejected(self):
        for row in ('202611120900 108 36 2 36 3', '202611121500 108 36 2 36 3',
                    '202611121400 108 36 -9 36 3', '202611121400 108 36 nan 36 3', '<html>denied</html>'):
            with self.assertRaises(wind.WindError):
                wind.parse_winds(row, self.stations, self.now)

    def test_duplicate_records_use_latest_station_time(self):
        data = wind.parse_winds('202611121400 108 9 4.0 9 6\n202611121300 108 27 2.0 27 3', self.stations, self.now)
        self.assertEqual(data[0]['speed'], 4)

    def test_shared_cache_and_failed_request_cooldown(self):
        with patch.object(wind, '_load_summary', return_value={'stations': []}) as load:
            self.assertEqual(wind.summary(), wind.summary())
            self.assertEqual(load.call_count, 1)
        wind._cache.clear()
        with patch.object(wind, '_load_summary', side_effect=wind.WindError('denied', 'provider_forbidden')) as load:
            for _ in range(2):
                with self.assertRaises(wind.WindError):
                    wind.summary()
            self.assertEqual(load.call_count, 1)

    def test_public_route_error_is_safe_and_not_cached(self):
        app = FastAPI()
        app.include_router(router)
        with patch.object(wind, 'summary', side_effect=wind.WindError('SFC 승인 확인', 'provider_forbidden')):
            response = TestClient(app).get('/api/weather/wind/stations')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error_code'], 'provider_forbidden')
        self.assertEqual(response.headers['cache-control'], 'no-store')
