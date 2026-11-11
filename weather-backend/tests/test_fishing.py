import os
import unittest
from unittest.mock import Mock, patch

import requests
from fastapi.testclient import TestClient

from app.fishing import providers, service, tides
from app.fishing.areas import FISHING_AREAS, get_area
from app.fishing.cache import TTLCache
from app.main import app

FORECAST_TEXT = """# REG_ID, TM_FC, TM_EF, MOD, NE, STN, C, MAN_ID, MAN_FC, W1, T, W2, WS1, WS2, WH1, WH2, SKY, PREP, WF
12D00000, 2099010105, 2099010112, A01, 1, 1, 1, 1, 1, N, 1, NE, 6, 9, 1.0, 1.5, DB01, 0, 맑음
12D00000, 2099010105, 2099010200, A01, 1, 1, 1, 1, 1, N, 1, NE, 8, 12, 1.5, 2.5, DB03, 1, 비
12D00000, 2099010017, 2099010112, A01, 1, 1, 1, 1, 1, N, 1, NE, 1, 2, 0.5, 0.5, DB01, 0, 이전발표
"""
BUOY_TEXT = """# TP, TM, STN_ID, STN_KO, LON, LAT, WH, WD, WS, WS_GST, TW, TA, PA, HM
B, 209901011200, 22107, 먼부이, 129.0, 35.0, 1.2, 45, 5.0, 7.0, 18.0, 15.0, 1015, 70
B, 209901011200, 22105, 가까운부이, 126.5, 34.0, 0.8, 30, 3.0, 4.0, 19.0, 16.0, 1016, 65
B, 209901011200, 22999, 결측부이, 126.4, 33.9, -99, 30, 3.0, 4.0, 19.0, 16.0, 1016, 65
L, 209901011200, 955, 등표, 126.4, 33.9, 0.5, 30, 3.0, 4.0, 19.0, 16.0, 1016, 65
"""
TIDE_JSON = {"body": {"items": {"item": [{"obsvtrNm": "추자도", "obsrvnDt": "2099-01-01 13:00", "obsrvnHgt": "150"}]}}}


def text_response(text):
    response = Mock(text=text)
    response.raise_for_status.return_value = None
    return response


def json_response(data):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = data
    return response


def fake_fetch(calls):
    def fetch(url, params, timeout=15):
        calls.append(url)
        if url.endswith('fct_afs_do.php'):
            return text_response(FORECAST_TEXT)
        if url.endswith('sea_obs.php'):
            return text_response(BUOY_TEXT)
        if 'tidebed' in url:
            return json_response(TIDE_JSON)
        raise AssertionError(url)
    return fetch


@patch.dict(os.environ, {'KMA_API_HUB_KEY': 'k', 'KHOA_TIDEBED_API_KEY': 't'})
class FishingSummaryTests(unittest.TestCase):
    def setUp(self):
        service.cache.clear()

    def summary(self):
        return service.get_fishing_summary('추자도 해상', '12D00000', 33.96194, 126.44527, area_id='chuja')

    def test_combines_latest_forecast_nearest_buoys_and_tide(self):
        calls = []
        with patch.object(providers, '_fetch', side_effect=fake_fetch(calls)):
            result = self.summary()
        forecast = result['sources']['forecast']['records']
        self.assertEqual([row['WF'] for row in forecast], ['맑음', '비'])  # 최신 발표만, 지난 발표 제외
        buoys = result['sources']['buoy']['records']
        self.assertEqual([row['STN_KO'] for row in buoys], ['가까운부이', '먼부이'])  # 거리순, 결측·비부이 제외
        self.assertIsNotNone(buoys[0]['distance_km'])
        self.assertEqual(result['sources']['tide']['data'], TIDE_JSON)
        self.assertEqual(result['sources']['tide']['predictions'], [{'time': '2099-01-01T13:00+09:00', 'height_cm': 150.0, 'station': '추자도'}])
        self.assertEqual(result['sources']['tide']['station'], '추자도')
        self.assertEqual(result['errors'], {})
        self.assertEqual(result['region_name'], '제주도 전해상')
        self.assertEqual(result['area_id'], 'chuja')

    def test_second_request_uses_cache_instead_of_calling_providers_again(self):
        calls = []
        with patch.object(providers, '_fetch', side_effect=fake_fetch(calls)):
            self.summary()
            first = len(calls)
            again = self.summary()
        self.assertEqual(first, 4)  # 해상예보, 부이, 오늘 조위, 내일 조위
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(again['sources']['buoy']['records']), 2)

    def test_cached_buoy_rows_are_not_mutated_between_areas(self):
        with patch.object(providers, '_fetch', side_effect=fake_fetch([])):
            jeju = service.get_fishing_summary('a', '12D00000', 33.96, 126.44)
            busan = service.get_fishing_summary('b', '12D00000', 35.1, 129.2)
        self.assertEqual(jeju['sources']['buoy']['records'][0]['STN_KO'], '가까운부이')
        self.assertEqual(busan['sources']['buoy']['records'][0]['STN_KO'], '먼부이')

    def test_one_provider_failure_keeps_other_sources(self):
        def fetch(url, params, timeout=15):
            if url.endswith('sea_obs.php'):
                raise requests.ConnectionError('down')
            return fake_fetch([])(url, params, timeout)
        with patch.object(providers, '_fetch', side_effect=fetch):
            result = self.summary()
        self.assertEqual(result['errors'], {'buoy': '해양 관측 자료를 가져오지 못했습니다.'})
        self.assertEqual(len(result['sources']['forecast']['records']), 2)

    def test_failures_are_not_cached(self):
        def broken(url, params, timeout=15):
            raise requests.Timeout('slow')
        with patch.object(providers, '_fetch', side_effect=broken):
            failed = self.summary()
        self.assertEqual(set(failed['errors']), {'forecast', 'buoy', 'tide'})
        with patch.object(providers, '_fetch', side_effect=fake_fetch([])):
            recovered = self.summary()
        self.assertEqual(recovered['errors'], {})

    def test_unreadable_tide_response_is_reported(self):
        def fetch(url, params, timeout=15):
            if 'tidebed' in url:
                response = json_response(None)
                response.json.side_effect = ValueError('not json')
                return response
            return fake_fetch([])(url, params, timeout)
        with patch.object(providers, '_fetch', side_effect=fetch):
            result = self.summary()
        self.assertEqual(result['errors'], {'tide': '조위 API 응답 형식을 읽지 못했습니다.'})

    @patch.dict(os.environ, {'KHOA_TIDEBED_API_KEY': ''})
    def test_missing_keys_raise(self):
        with self.assertRaises(RuntimeError):
            self.summary()


    def test_tomorrow_tide_failure_keeps_today(self):
        def fetch(url, params, timeout=15):
            if 'tidebed' in url and params['reqDate'] != service.datetime.now(providers.SEOUL).strftime('%Y%m%d'):
                raise requests.Timeout('slow')
            return fake_fetch([])(url, params, timeout)
        with patch.object(providers, '_fetch', side_effect=fetch):
            result = self.summary()
        self.assertEqual(result['errors'], {})
        self.assertEqual(len(result['sources']['tide']['predictions']), 1)


class TideTests(unittest.TestCase):
    @staticmethod
    def raw(heights, day='2099-01-01', start=0):
        return {'body': {'items': {'item': [
            {'obsvtrNm': '추자도', 'obsrvnDt': f'{day} {start + i:02d}:00', 'obsrvnHgt': str(h)} for i, h in enumerate(heights)]}}}

    def test_merges_days_sorted_and_skips_bad_rows(self):
        today = self.raw([100, 120])
        tomorrow = self.raw([90], day='2099-01-02')
        bad = {'body': {'items': {'item': {'obsrvnDt': 'x', 'obsrvnHgt': '1'}}}}
        points = tides.normalize_predictions(tomorrow, today, bad, None)
        self.assertEqual([p['time'] for p in points], ['2099-01-01T00:00+09:00', '2099-01-01T01:00+09:00', '2099-01-02T00:00+09:00'])

    def test_finds_high_and_low_with_sub_hour_estimate(self):
        points = tides.normalize_predictions(self.raw([100, 200, 260, 250, 180, 90, 40, 60, 120]))
        extremes = tides.find_extremes(points)
        self.assertEqual([e['type'] for e in extremes], ['high', 'low'])
        high, low = extremes
        # 200, 260, 250 → 꼭짓점은 02시보다 조금 뒤 (02:21), 높이는 260보다 조금 높음
        self.assertEqual(high['time'], '2099-01-01T02:21+09:00')
        self.assertGreater(high['height_cm'], 260)
        self.assertTrue(low['time'].startswith('2099-01-01T05:') or low['time'].startswith('2099-01-01T06:'))
        self.assertLess(low['height_cm'], 40.1)

    def test_skips_gaps_and_flat_edges(self):
        points = tides.normalize_predictions(self.raw([100, 200]), self.raw([150], start=5))
        self.assertEqual(tides.find_extremes(points), [])


class TTLCacheTests(unittest.TestCase):
    def test_expires_after_ttl(self):
        now = [0.0]
        cache = TTLCache(clock=lambda: now[0])
        loader = Mock(side_effect=[1, 2])
        self.assertEqual(cache.get_or_load('k', 10, loader), (1, False))
        self.assertEqual(cache.get_or_load('k', 10, loader), (1, True))
        now[0] = 11
        self.assertEqual(cache.get_or_load('k', 10, loader), (2, False))


@patch.dict(os.environ, {'KMA_API_HUB_KEY': 'k', 'KHOA_TIDEBED_API_KEY': 't'})
class FishingApiTests(unittest.TestCase):
    def setUp(self):
        service.cache.clear()
        self.client = TestClient(app)

    def test_areas_endpoint_lists_catalog(self):
        response = self.client.get('/api/fishing/areas')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.json()], [area['id'] for area in FISHING_AREAS])

    def test_summary_by_area_id_uses_catalog_values(self):
        with patch.object(providers, '_fetch', side_effect=fake_fetch([])) as fetch:
            response = self.client.get('/api/fishing/summary', params={'area': 'west'})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        west = get_area('west')
        self.assertEqual((body['place'], body['region'], body['area_id']), (west['place'], west['region'], 'west'))
        forecast_params = next(call.args[1] for call in fetch.call_args_list if call.args[0].endswith('fct_afs_do.php'))
        self.assertEqual(forecast_params['reg'], west['region'])

    def test_unknown_area_is_404(self):
        self.assertEqual(self.client.get('/api/fishing/summary', params={'area': 'nowhere'}).status_code, 404)

    def test_legacy_query_still_works(self):
        with patch.object(providers, '_fetch', side_effect=fake_fetch([])):
            response = self.client.get('/api/fishing/summary', params={'place': '태안 앞바다', 'region': '12A20100', 'lat': 36.7, 'lon': 126.1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['region_name'], '서해중부앞바다')

    def test_invalid_coordinates_are_rejected(self):
        self.assertEqual(self.client.get('/api/fishing/summary', params={'lat': 999}).status_code, 422)


if __name__ == '__main__':
    unittest.main()
