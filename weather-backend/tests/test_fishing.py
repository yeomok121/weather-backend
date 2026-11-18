import os
import unittest
from unittest.mock import Mock, patch

import requests
from fastapi.testclient import TestClient

from app.fishing import fishing_index, providers, service, tides, warnings, zones
from app.fishing.areas import FISHING_AREAS, get_area
from app.fishing.cache import TTLCache
from app.main import app

FORECAST_TEXT = """# REG_ID, TM_FC, TM_EF, MOD, NE, STN, C, MAN_ID, MAN_FC, W1, T, W2, WS1, WS2, WH1, WH2, SKY, PREP, WF
12B10201, 2099010105, 2099010112, A01, 1, 1, 1, 1, 1, N, 1, NE, 6, 9, 1.0, 1.5, DB01, 0, 맑음
12B10201, 2099010105, 2099010200, A01, 1, 1, 1, 1, 1, N, 1, NE, 8, 12, 1.5, 2.5, DB03, 1, 비
12B10201, 2099010017, 2099010112, A01, 1, 1, 1, 1, 1, N, 1, NE, 1, 2, 0.5, 0.5, DB01, 0, 이전발표
"""
BUOY_TEXT = """# TP, TM, STN_ID, STN_KO, LON, LAT, WH, WD, WS, WS_GST, TW, TA, PA, HM
B, 209901011200, 22107, 먼부이, 129.0, 35.0, 1.2, 45, 5.0, 7.0, 18.0, 15.0, 1015, 70
B, 209901011200, 22105, 가까운부이, 126.5, 34.0, 0.8, 30, 3.0, 4.0, 19.0, 16.0, 1016, 65
B, 209901011200, 22999, 결측부이, 126.4, 33.9, -99, 30, 3.0, 4.0, 19.0, 16.0, 1016, 65
L, 209901011200, 955, 등표, 126.4, 33.9, 0.5, 30, 3.0, 4.0, 19.0, 16.0, 1016, 65
"""
def index_row(point, lat, lot, date, noon, species, grade):
    # 실제 응답(2026-11-17, gubun=갯바위)과 같은 형식
    return {"seafsPstnNm": point, "lat": lat, "lot": lot, "predcYmd": date, "predcNoonSeCd": noon, "seafsTgfshNm": species,
            "tdlvHrCn": "소조기", "minWvhgt": 0.6, "maxWvhgt": 0.9, "minWtem": 16.0, "maxWtem": 16.1, "minArtmp": 14.7, "maxArtmp": 16.3,
            "minCrsp": 0.2, "maxCrsp": 0.6, "minWspd": 7.8, "maxWspd": 10.3, "totalIndex": grade}


INDEX_ROWS = [
    index_row("가거도", 34.07308, 125.08805, "2099-01-01", "오전", "감성돔", "보통"),
    index_row("추자도", 33.95, 126.30, "2099-01-01", "오후", "참돔", "좋음"),
    index_row("추자도", 33.95, 126.30, "2099-01-01", "오전", "기타어종", "매우좋음"),
    index_row("추자도", 33.95, 126.30, "2099-01-01", "오전", "돌돔", "나쁨"),
    index_row("추자도", 33.95, 126.30, "2099-01-01", "오전", "벵에돔", "매우좋음"),
    index_row("추자도", 33.95, 126.30, "2099-01-02", "오전", "돌돔", "좋음"),
]
INDEX_JSON = {"header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
              "body": {"items": {"item": INDEX_ROWS}, "pageNo": 1, "numOfRows": 1000, "totalCount": len(INDEX_ROWS), "type": "json"}}
# 2026-11-17 실제 특보현황 응답(disp=0, help=1) 일부
WARNING_TEXT = """#START7777
#---------------------------------------------------------------------------------------------------
#  특보현황 조회
#  7. WRN       : 특보종류
# REG_UP  REG_UP_KO-------------------------------  REG_ID    REG_KO----------------------------------  TM_FC         TM_EF         WRN     LVL       CMD   ED_TM
S1320000, 남해서부전해상                          , S1322100, 남해서부서쪽먼바다                      , 202611171700, 202611171900, 풍랑  , 주의    , 발표, 21일 오전(09시~12시),=
S1310000, 남해동부전해상                          , S1312010, 남해동부안쪽먼바다                      , 202611171600, 202611181758, 풍랑  , 예비    , 발표,  ,=
S1323000, 제주도앞바다                            , S1323100, 제주도북부앞바다                        , 202611171600, 202611181758, 풍랑  , 예비    , 발표,  ,=
S1251000, 서해중부앞바다                          , S1251300, 충남북부앞바다                          , 202611171600, 202611181758, 풍랑  , 예비    , 발표,  ,=
L1030000, 충청남도                                , L1031100, 태안군                                  , 202611171600, 202611181758, 강풍  , 예비    , 발표,  ,=
L1090000, 제주도                                  , L1091000, 추자도                                  , 202611171600, 202611181758, 강풍  , 예비    , 발표,  ,=
L1091400, 서귀포시(산지 제외)                     , L1091430, 서귀포시동부                            , 202611171635, 202611171635, 호우  , 주의    , 발표,  ,=
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
        if 'fcstFishing' in url:
            return json_response(INDEX_JSON)
        if url.endswith('wrn_now_data.php'):
            return text_response(WARNING_TEXT)
        raise AssertionError(url)
    return fetch


@patch.dict(os.environ, {'KMA_API_HUB_KEY': 'k', 'KHOA_TIDEBED_API_KEY': 't', 'KHOA_FISHING_API_KEY': 'f'})
class FishingSummaryTests(unittest.TestCase):
    def setUp(self):
        service.cache.clear()

    def summary(self):
        return service.get_fishing_summary('추자도 해상', '12B10201', 33.96194, 126.44527, area_id='chuja')

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
        self.assertEqual(result['region_name'], '남해서부서쪽먼바다')
        self.assertEqual(result['area_id'], 'chuja')

    def test_second_request_uses_cache_instead_of_calling_providers_again(self):
        calls = []
        with patch.object(providers, '_fetch', side_effect=fake_fetch(calls)):
            self.summary()
            first = len(calls)
            again = self.summary()
        self.assertEqual(first, 6)  # 해상예보, 부이, 오늘 조위, 내일 조위, 바다낚시지수, 기상특보
        self.assertEqual(len(calls), 6)
        self.assertEqual(len(again['sources']['buoy']['records']), 2)

    def test_cached_buoy_rows_are_not_mutated_between_areas(self):
        with patch.object(providers, '_fetch', side_effect=fake_fetch([])):
            jeju = service.get_fishing_summary('a', '12B10201', 33.96, 126.44)
            busan = service.get_fishing_summary('b', '12B10201', 35.1, 129.2)
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
        self.assertEqual(set(failed['errors']), {'forecast', 'buoy', 'tide', 'fishing_index', 'warnings'})
        self.assertIsNone(failed['sources']['warnings']['items'])  # 못 받았으면 '특보 없음'이 아니라 None
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


    def test_fishing_index_for_nearest_point(self):
        with patch.object(providers, '_fetch', side_effect=fake_fetch([])):
            index = self.summary()['sources']['fishing_index']
        self.assertEqual(index['point']['name'], '추자도')
        self.assertEqual(index['gubun'], '갯바위')
        self.assertEqual([day['date'] for day in index['days']], ['2099-01-01', '2099-01-02'])
        morning, afternoon = index['days'][0]['sessions']
        self.assertEqual((morning['session'], afternoon['session']), ('오전', '오후'))
        # 좋은 지수부터, 같은 지수면 기타어종은 뒤로
        self.assertEqual([(s['name'], s['index']) for s in morning['species']], [('벵에돔', '매우좋음'), ('기타어종', '매우좋음'), ('돌돔', '나쁨')])
        self.assertEqual(morning['wave_m'], {'min': 0.6, 'max': 0.9})
        self.assertEqual(morning['tide_phase'], '소조기')

    @patch.dict(os.environ, {'KHOA_FISHING_API_KEY': ''})
    def test_missing_fishing_index_key_keeps_other_sources(self):
        calls = []
        with patch.object(providers, '_fetch', side_effect=fake_fetch(calls)):
            result = self.summary()
        self.assertEqual(result['errors'], {'fishing_index': '바다낚시지수 API 설정이 아직 완료되지 않았습니다.'})
        self.assertFalse(any('fcstFishing' in url for url in calls))
        self.assertEqual(len(result['sources']['buoy']['records']), 2)

    def test_unsubscribed_fishing_index_response_is_an_error_not_empty_data(self):
        def fetch(url, params, timeout=15):
            if 'fcstFishing' in url:
                return json_response({"result": {"status": 403, "message": "활용신청이 필요한 API 입니다."}})
            return fake_fetch([])(url, params, timeout)
        with patch.object(providers, '_fetch', side_effect=fetch):
            result = self.summary()
        self.assertEqual(result['errors'], {'fishing_index': '바다낚시지수를 가져오지 못했습니다.'})


    def test_area_warnings_are_matched_and_sorted(self):
        with patch.object(providers, '_fetch', side_effect=fake_fetch([])):
            result = self.summary()  # 추자도 해상
        items = result['sources']['warnings']['items']
        self.assertEqual([(w['region'], w['title']) for w in items], [
            ('남해서부서쪽먼바다', '풍랑주의보'), ('추자도', '강풍 예비특보')])
        self.assertEqual(items[0]['release_hint'], '21일 오전(09시~12시)')
        self.assertEqual(items[0]['issued_at'], '2026-11-17T17:00+09:00')
        self.assertEqual(items[0]['effective'], '17일 19:00')
        self.assertEqual(items[1]['effective'], '18일 오후(12시~18시)')  # 예비특보 약속 시각

    def test_warning_api_not_subscribed_is_an_error(self):
        def fetch(url, params, timeout=15):
            if url.endswith('wrn_now_data.php'):
                return text_response('{ "result" : { "status" : 403, "message" : "활용신청이 필요한 API 입니다." } }')
            return fake_fetch([])(url, params, timeout)
        with patch.object(providers, '_fetch', side_effect=fetch):
            result = self.summary()
        self.assertEqual(set(result['errors']), {'warnings'})
        self.assertIsNone(result['sources']['warnings']['items'])


class WarningTests(unittest.TestCase):
    def test_parses_real_format_and_matches_other_areas(self):
        rows = warnings.parse_warnings(WARNING_TEXT)
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0]['REG_KO'], '남해서부서쪽먼바다')
        self.assertEqual(rows[1]['ED_TM'], '')
        west = warnings.warnings_for_regions(rows, get_area('west')['warning_regions'])
        self.assertEqual([w['title'] for w in west], ['강풍 예비특보', '풍랑 예비특보'])
        busan = warnings.warnings_for_regions(rows, get_area('southeast')['warning_regions'])
        self.assertEqual([w['region'] for w in busan], ['남해동부안쪽먼바다'])

    def test_lifted_warnings_and_empty_keywords_are_ignored(self):
        rows = warnings.parse_warnings(WARNING_TEXT.replace('풍랑  , 주의    , 발표', '풍랑  , 주의    , 해제'))
        self.assertNotIn('남해서부서쪽먼바다', [w['region'] for w in warnings.warnings_for_regions(rows, ['남해서부서쪽먼바다'])])
        self.assertEqual(warnings.warnings_for_regions(rows, ['']), [])


class FishingIndexTests(unittest.TestCase):
    def test_reads_all_pages_until_total_count(self):
        pages = [INDEX_ROWS[:4], INDEX_ROWS[4:]]
        def fetch(url, params, timeout=15):
            page = int(params['pageNo'])
            return json_response({"header": {"resultCode": "00"}, "body": {"items": {"item": pages[page - 1]}, "totalCount": len(INDEX_ROWS)}})
        with patch.object(providers, '_fetch', side_effect=fetch) as mocked:
            items = providers.fetch_fishing_index(page_size=4)
        self.assertEqual(len(items), len(INDEX_ROWS))
        self.assertEqual(mocked.call_count, 2)

    def test_real_points_match_each_area(self):
        # 2026-11-17 실제 응답(갯바위, 49개 포인트) 중 해역 근처 포인트 좌표
        real = [("추자도", 33.96186, 126.30037), ("서망항남측(27km)", 34.119, 126.152), ("모항항", 36.77966, 126.12952),
                ("안흥항북북서(27km)", 36.9, 126.035), ("부산동부", 35.1851, 129.2288), ("부산남부", 35.0438, 129.0774),
                ("가거도", 34.07308, 125.08805), ("울릉도", 37.4925, 130.9141)]
        rows = [{"seafsPstnNm": name, "lat": lat, "lot": lot} for name, lat, lot in real]
        expected = {"chuja": "추자도", "west": "모항항", "southeast": "부산동부"}
        for area_id, point in expected.items():
            area = get_area(area_id)
            self.assertEqual(fishing_index.summarize(rows, area["lat"], area["lon"])["point"]["name"], point)

    def test_no_point_within_range(self):
        result = fishing_index.summarize(INDEX_ROWS, 37.5, 129.5)  # 동해, 80km 안에 포인트 없음
        self.assertIsNone(result['point'])
        self.assertEqual(result['days'], [])
        self.assertEqual(len(result['nearby']), 2)


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


class ZoneTests(unittest.TestCase):
    REAL_POINTS = ["가거도", "강릉항동(2km)", "거문도", "거제도", "거진항동측(2km)", "계마항", "공현진항동남동(5km)", "국화도", "김녕",
                   "남애항", "대진항", "도두항북서(2km)", "모항항", "목포북항서측(53km)", "방포항", "부산남부", "부산동부", "부산서부",
                   "비금도", "비양도", "상왕등도", "상태도", "서귀포", "서망항남측(27km)", "성산포", "신시도", "신지도", "아야진항",
                   "안흥항북북서(27km)", "안흥항북측(30km)", "안흥항서측(40km)", "양포항남동(3km)", "어청도", "연도", "영흥도", "외옹치항",
                   "욕지도", "울릉도", "울산", "울진 후정", "인천항서측(24km)", "임원항동북동(10km)", "척포항남남동(24km)",
                   "척포항남남서(25km)", "추자도", "포항", "하리항남서(14km)", "하조도", "후포"]  # 2026-11-17 실제 49개

    def test_every_real_point_has_a_zone(self):
        self.assertEqual(len(self.REAL_POINTS), 49)
        missing = [name for name in self.REAL_POINTS if zones.zone_for_point(name) is None]
        self.assertEqual(missing, [])

    def test_zone_details(self):
        chuja = zones.zone_for_point("추자도")
        self.assertEqual((chuja["forecast_reg"], chuja["sea"], chuja["tide_system"]), ("12B10201", "남해", 8))
        self.assertEqual(chuja["warning_regions"], ["남해서부서쪽먼바다", "추자도"])
        self.assertEqual(zones.zone_for_point("모항항")["tide_system"], 7)
        self.assertIsNone(zones.zone_for_point("없는포인트"))

    def test_warning_keywords_match_coastal_and_unseparated_names(self):
        rows = warnings.parse_warnings(WARNING_TEXT + "S2330100, 남해서부서쪽먼바다중 추자도연안바다 , S2330100, 남해서부서쪽먼바다중 추자도연안바다, 202611171600, 202611181758, 풍랑  , 예비    , 발표,  ,=\n"
                                       "S2211700, 인천경기남부앞바다중 평수구역 , S2211700, 인천경기남부앞바다중 남부앞평수구역, 202611171600, 202611181758, 풍랑  , 예비    , 발표,  ,=\n")
        chuja = warnings.warnings_for_regions(rows, zones.zone_for_point("추자도")["warning_regions"])
        self.assertIn("남해서부서쪽먼바다중 추자도연안바다", [w["region"] for w in chuja])
        incheon = warnings.warnings_for_regions(rows, zones.zone_for_point("영흥도")["warning_regions"])
        self.assertEqual([w["region"] for w in incheon], ["인천경기남부앞바다중 남부앞평수구역"])


@patch.dict(os.environ, {'KMA_API_HUB_KEY': 'k', 'KHOA_TIDEBED_API_KEY': 't', 'KHOA_FISHING_API_KEY': 'f'})
class PointSummaryTests(unittest.TestCase):
    def setUp(self):
        service.cache.clear()

    def test_points_list_has_zone_and_sea(self):
        with patch.object(providers, '_fetch', side_effect=fake_fetch([])):
            points = service.get_fishing_points()
        chuja = next(p for p in points if p['id'] == '추자도')
        self.assertEqual((chuja['sea'], chuja['zone'], chuja['tide_system']), ('남해', '남해서부서쪽먼바다', 8))
        self.assertEqual([p['sea'] for p in points], ['서해', '남해'])  # 서해 → 남해 → 제주 → 동해 순

    def test_summary_for_point_uses_point_location_and_zone(self):
        calls = []
        def fetch(url, params, timeout=15):
            calls.append((url, dict(params)))
            return fake_fetch([])(url, params, timeout)
        with patch.object(providers, '_fetch', side_effect=fetch):
            result = service.summary_for_point('추자도')
        self.assertEqual((result['point_id'], result['place'], result['tide_system']), ('추자도', '추자도', 8))
        self.assertEqual(result['coordinates'], {'lat': 33.95, 'lon': 126.3})
        self.assertEqual(result['sources']['fishing_index']['point']['distance_km'], 0.0)
        self.assertEqual(next(p for u, p in calls if u.endswith('fct_afs_do.php'))['reg'], '12B10201')
        self.assertEqual(result['region_name'], '남해서부서쪽먼바다')
        self.assertIsNone(service.summary_for_point('없는포인트'))

    def test_point_without_zone_skips_forecast_and_warnings_with_message(self):
        with patch.object(providers, '_fetch', side_effect=fake_fetch([])):
            result = service.summary_for_point('가거도')  # 테스트용 응답의 가거도는 연결이 있지만 구역을 지워 봄
        self.assertNotIn('forecast', result['errors'])
        with patch.dict(zones.POINT_ZONES, {'가거도': '없는구역'}), patch.object(providers, '_fetch', side_effect=fake_fetch([])):
            service.cache.clear()
            result = service.summary_for_point('가거도')
        self.assertIn('해상예보 구역이 아직 설정되지 않았습니다', result['errors']['forecast'])
        self.assertIn('기상특보 구역이 아직 설정되지 않았습니다', result['errors']['warnings'])
        self.assertIsNone(result['sources']['warnings']['items'])

    def test_forecast_falls_back_to_parent_zone_when_empty(self):
        regs = []
        def fetch(url, params, timeout=15):
            if url.endswith('fct_afs_do.php'):
                regs.append(params['reg'])
                return text_response(FORECAST_TEXT if params['reg'] == '12B10000' else '#START7777\n#7777END\n')
            return fake_fetch([])(url, params, timeout)
        with patch.object(providers, '_fetch', side_effect=fetch):
            result = service.summary_for_point('추자도')
        self.assertEqual(regs, ['12B10201', '12B10000'])
        self.assertEqual(len(result['sources']['forecast']['records']), 2)

    def test_points_and_point_summary_api(self):
        client = TestClient(app)
        with patch.object(providers, '_fetch', side_effect=fake_fetch([])):
            points = client.get('/api/fishing/points')
            summary = client.get('/api/fishing/summary', params={'point': '추자도'})
            missing = client.get('/api/fishing/summary', params={'point': '없는포인트'})
        self.assertEqual(points.status_code, 200)
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()['point_id'], '추자도')
        self.assertEqual(missing.status_code, 404)


class TTLCacheTests(unittest.TestCase):
    def test_expires_after_ttl(self):
        now = [0.0]
        cache = TTLCache(clock=lambda: now[0])
        loader = Mock(side_effect=[1, 2])
        self.assertEqual(cache.get_or_load('k', 10, loader), (1, False))
        self.assertEqual(cache.get_or_load('k', 10, loader), (1, True))
        now[0] = 11
        self.assertEqual(cache.get_or_load('k', 10, loader), (2, False))


@patch.dict(os.environ, {'KMA_API_HUB_KEY': 'k', 'KHOA_TIDEBED_API_KEY': 't', 'KHOA_FISHING_API_KEY': 'f'})
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


class FakeCursor:
    def __init__(self, store):
        self.store, self.rows = store, []
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False
    def execute(self, sql, params=()):
        if sql.lstrip().startswith("SELECT point_name"):
            self.rows = [{"point_name": name} for name in self.store.get(params[0], [])]
        elif sql.lstrip().startswith("DELETE FROM user_fishing_points"):
            self.store[params[0]] = []
    def executemany(self, sql, rows):
        for user_id, name, _ in rows:
            self.store.setdefault(user_id, []).append(name)
    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, store):
        self.store, self.committed = store, False
    def cursor(self):
        return FakeCursor(self.store)
    def commit(self):
        self.committed = True
    def close(self):
        pass


class FishingFavoritesApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.store = {}
        from app.fishing import favorites
        favorites._schema_ready = False

    def call(self, method, json=None, user={"id": 7}):
        from app.routers import fishing as router
        with patch.object(router, 'get_conn', side_effect=lambda: FakeConn(self.store)), \
             patch.object(router, '_get_token_user', side_effect=(lambda cur, auth: user) if user else HTTPException_401):
            return self.client.request(method, '/api/users/me/fishing-points', json=json, headers={'Authorization': 'Bearer t'})

    def test_save_and_read_keeps_order_and_removes_duplicates(self):
        saved = self.call('PUT', {'points': ['추자도', ' 모항항 ', '추자도']})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()['points'], ['추자도', '모항항'])
        self.assertEqual(self.call('GET').json(), {'points': ['추자도', '모항항']})

    def test_rejects_too_many_or_blank(self):
        self.assertEqual(self.call('PUT', {'points': [f'p{i}' for i in range(11)]}).status_code, 400)
        self.assertEqual(self.call('PUT', {'points': ['  ']}).status_code, 400)

    def test_requires_login(self):
        self.assertEqual(self.call('GET', user=None).status_code, 401)



class GearCursor:
    """user_fishing_gear 용 가짜 커서: {user_id: setup_json}"""
    def __init__(self, store):
        self.store, self.row = store, None
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False
    def execute(self, sql, params=()):
        if sql.lstrip().startswith("SELECT setup_json"):
            self.row = {"setup_json": self.store[params[0]], "updated_at": None} if params[0] in self.store else None
        elif sql.lstrip().startswith("INSERT INTO user_fishing_gear"):
            self.store[params[0]] = params[1]
    def fetchone(self):
        return self.row


class FishingGearApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.store = {}
        from app.fishing import gear
        gear._schema_ready = False

    def call(self, method, json=None, user={"id": 7}):
        from app.routers import fishing as router
        conn = FakeConn(self.store)
        conn.cursor = lambda: GearCursor(self.store)
        with patch.object(router, 'get_conn', return_value=conn), \
             patch.object(router, '_get_token_user', side_effect=(lambda cur, auth: user) if user else HTTPException_401):
            return self.client.request(method, '/api/users/me/fishing-gear', json=json, headers={'Authorization': 'Bearer t'})

    def test_empty_until_saved_then_same_setup_everywhere(self):
        self.assertEqual(self.call('GET').json(), {'items': [], 'hidden': [], 'updated_at': None})
        body = {'items': [{'id': 'my-a1', 'name': ' 루어 박스 ', 'note': '스푼·지그'}, {'id': 'my-b2', 'name': '갯바위 방석'},
                          {'id': 'my-c3', 'name': '루어 박스'}],  # 같은 이름은 하나만
                'hidden': ['bait', 'bait', 'trash']}
        saved = self.call('PUT', body)
        self.assertEqual(saved.status_code, 200)
        expected = {'items': [{'id': 'my-a1', 'name': '루어 박스', 'note': '스푼·지그'}, {'id': 'my-b2', 'name': '갯바위 방석', 'note': ''}],
                    'hidden': ['bait', 'trash']}
        self.assertEqual({k: saved.json()[k] for k in ('items', 'hidden')}, expected)
        self.assertEqual(self.call('GET').json(), {**expected, 'updated_at': None})

    def test_rejects_bad_input(self):
        self.assertEqual(self.call('PUT', {'items': [{'id': 'my-a', 'name': '   '}]}).status_code, 400)
        self.assertEqual(self.call('PUT', {'items': [{'id': 'my-a', 'name': '가' * 31}]}).status_code, 400)
        self.assertEqual(self.call('PUT', {'items': [{'id': '<script>', 'name': '칼'}]}).status_code, 400)
        self.assertEqual(self.call('PUT', {'items': [{'id': f'my-{i}', 'name': f'항목{i}'} for i in range(31)]}).status_code, 400)
        self.assertEqual(self.call('PUT', {'hidden': ['Bad Id']}).status_code, 400)
        self.assertEqual(self.store, {})

    def test_requires_login(self):
        self.assertEqual(self.call('GET', user=None).status_code, 401)
        self.assertEqual(self.call('PUT', {'items': []}, user=None).status_code, 401)

    def test_broken_stored_json_reads_as_empty(self):
        self.store[7] = 'not json'
        self.assertEqual(self.call('GET').json()['items'], [])

def HTTPException_401(cur, auth):
    from fastapi import HTTPException
    raise HTTPException(status_code=401, detail="로그인이 필요합니다")


TIDE_INVALID = {"header": {"resultCode": "10", "resultMsg": "INVALID_REQUEST_PARAMETER_ERROR"}}  # 2026-11-18 추자도 섬 좌표 실제 응답


@patch.dict(os.environ, {'KMA_API_HUB_KEY': 'k', 'KHOA_TIDEBED_API_KEY': 't', 'KHOA_FISHING_API_KEY': 'f'})
class TideLocationTests(unittest.TestCase):
    def setUp(self):
        service.cache.clear()

    def run_with_tide(self, accept):
        tide_calls = []
        def fetch(url, params, timeout=15):
            if 'tidebed' in url:
                tide_calls.append((params['lat'], params['lot']))
                return json_response(TIDE_JSON if accept(float(params['lat']), float(params['lot'])) else TIDE_INVALID)
            return fake_fetch([])(url, params, timeout)
        with patch.object(providers, '_fetch', side_effect=fetch):
            result = service.summary_for_point('추자도')
        return result, tide_calls

    def test_island_point_moves_toward_sea_until_tide_is_found(self):
        result, calls = self.run_with_tide(lambda lat, lon: lon > 126.33)  # 섬 위(126.30)는 거부, 약 4.6 km 동쪽은 허용
        self.assertEqual(result['errors'], {})
        self.assertEqual(len(result['sources']['tide']['predictions']), 1)
        location = result['sources']['tide']['location']
        self.assertEqual((location['lat'], location['lon']), (33.95, 126.35))
        self.assertAlmostEqual(location['shifted_km'], 4.6, delta=0.2)
        self.assertEqual(calls[0], ('33.95000', '126.30000'))
        # 다음 조회는 찾아 둔 좌표를 바로 씀
        _, again = self.run_with_tide(lambda lat, lon: lon > 126.33)
        self.assertEqual(again, [])

    def test_no_valid_tide_location_is_reported_not_silently_empty(self):
        result, calls = self.run_with_tide(lambda lat, lon: False)
        self.assertEqual(result['errors'], {'tide': '이 위치 근처에서 조위 예측을 찾지 못했습니다.'})
        self.assertEqual(len(calls), len(service.TIDE_OFFSETS) * 2)  # 오늘·내일 모두 시도했지만 모두 거부
        self.assertIsNone(result['sources']['tide']['location'])

    def test_tide_error_code_is_not_treated_as_success(self):
        with patch.object(providers, '_fetch', return_value=json_response({"header": {"resultCode": "30", "resultMsg": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR"}})):
            with self.assertRaises(ValueError):
                providers.fetch_tide_prediction(33.9, 126.3, '20991231')


@patch.dict(os.environ, {'KMA_API_HUB_KEY': 'k', 'KHOA_TIDEBED_API_KEY': 't', 'KHOA_FISHING_API_KEY': 'f'})
class RenamedPointTests(unittest.TestCase):
    def setUp(self):
        service.cache.clear()

    def test_unknown_point_inherits_nearest_zone_within_30km(self):
        renamed = dict(INDEX_ROWS[1], seafsPstnNm='추자도신항(1km)', lat=33.97, lot=126.31)   # 추자도에서 약 1 km
        far = dict(INDEX_ROWS[1], seafsPstnNm='먼바다포인트', lat=35.5, lot=124.5)
        def fetch(url, params, timeout=15):
            if 'fcstFishing' in url:
                rows = INDEX_ROWS + [renamed, far]
                return json_response({"header": {"resultCode": "00"}, "body": {"items": {"item": rows}, "totalCount": len(rows)}})
            return fake_fetch([])(url, params, timeout)
        with patch.object(providers, '_fetch', side_effect=fetch):
            points = {p['id']: p for p in service.get_fishing_points()}
            summary = service.summary_for_point('추자도신항(1km)')
        self.assertEqual((points['추자도신항(1km)']['zone'], points['추자도신항(1km)']['zone_from']), ('남해서부서쪽먼바다', '추자도'))
        self.assertEqual(points['먼바다포인트']['zone'], '')
        self.assertEqual(summary['region'], '12B10201')
        self.assertEqual(zones.zone_for_point('강릉항북동(2km)')['zone'], '강원중부앞바다')


class FetchRetryTests(unittest.TestCase):
    def ok(self, status=200):
        response = Mock(status_code=status)
        response.raise_for_status.side_effect = None if status < 400 else requests.HTTPError(response=response)
        return response

    @patch.object(providers.time, 'sleep')
    def test_retries_once_on_timeout_and_server_error(self, sleep):
        with patch.object(providers.requests, 'get', side_effect=[requests.Timeout('slow'), self.ok()]) as get:
            self.assertEqual(providers._fetch('https://x', {}).status_code, 200)
        self.assertEqual(get.call_count, 2)
        with patch.object(providers.requests, 'get', side_effect=[self.ok(503), self.ok()]) as get:
            self.assertEqual(providers._fetch('https://x', {}).status_code, 200)
        self.assertEqual(get.call_count, 2)

    @patch.object(providers.time, 'sleep')
    def test_gives_up_after_second_failure_and_does_not_retry_client_errors(self, sleep):
        with patch.object(providers.requests, 'get', side_effect=[requests.Timeout('a'), requests.Timeout('b')]):
            with self.assertRaises(requests.Timeout):
                providers._fetch('https://x', {})
        with patch.object(providers.requests, 'get', side_effect=[self.ok(404)]) as get:
            with self.assertRaises(requests.HTTPError):
                providers._fetch('https://x', {})
        self.assertEqual(get.call_count, 1)


@patch.dict(os.environ, {'KMA_API_HUB_KEY': 'k', 'KHOA_TIDEBED_API_KEY': 't', 'KHOA_FISHING_API_KEY': 'f'})
class TideDaysTests(unittest.TestCase):
    def setUp(self):
        service.cache.clear()

    def test_coordinate_search_runs_once_for_today_and_tomorrow(self):
        calls = []
        def fetch(url, params, timeout=15):
            if 'tidebed' in url:
                calls.append((params['reqDate'], params['lot']))
                return json_response(TIDE_JSON if float(params['lot']) > 126.33 else TIDE_INVALID)
            return fake_fetch([])(url, params, timeout)
        with patch.object(providers, '_fetch', side_effect=fetch):
            result = service.summary_for_point('추자도')
        # 오늘: 섬 좌표 거부 → 동쪽 5 km 허용 (2번), 내일: 찾아 둔 좌표로 바로 (1번)
        self.assertEqual([lot for _, lot in calls], ['126.30000', '126.35000', '126.35000'])
        self.assertEqual(result['errors'], {})

    def test_today_failure_keeps_tomorrow_and_location(self):
        today = service.datetime.now(providers.SEOUL).strftime('%Y%m%d')
        def fetch(url, params, timeout=15):
            if 'tidebed' in url and params['reqDate'] == today:
                raise requests.ConnectionError('down')
            return fake_fetch([])(url, params, timeout)
        with patch.object(providers, '_fetch', side_effect=fetch), patch.object(service, 'log_error') as logged:
            result = service.summary_for_point('추자도')
        self.assertEqual(result['errors'], {'tide': '오늘 조위를 가져오지 못해 내일 예측만 표시합니다.'})
        self.assertEqual(len(result['sources']['tide']['predictions']), 1)
        self.assertIsNotNone(result['sources']['tide']['location'])
        self.assertEqual(logged.call_args_list[0].args[0], 'fishing_tide_failed')

    def test_forecast_failure_is_logged_without_details(self):
        def fetch(url, params, timeout=15):
            if url.endswith('fct_afs_do.php'):
                raise requests.Timeout('slow')
            return fake_fetch([])(url, params, timeout)
        with patch.object(providers, '_fetch', side_effect=fetch), patch.object(service, 'log_error') as logged:
            result = service.summary_for_point('추자도')
        self.assertIn('forecast', result['errors'])
        self.assertEqual([c.args[0] for c in logged.call_args_list], ['fishing_forecast_failed'])
        self.assertEqual(logged.call_args_list[0].kwargs, {'region': '12B10201'})


class TodayGradeTests(unittest.TestCase):
    def test_best_grade_today_skips_finished_morning(self):
        from datetime import datetime
        rows = [
            index_row("추자도", 33.95, 126.30, "2099-01-01", "오전", "벵에돔", "매우좋음"),
            index_row("추자도", 33.95, 126.30, "2099-01-01", "오후", "참돔", "보통"),
            index_row("추자도", 33.95, 126.30, "2099-01-01", "오후", "돌돔", "나쁨"),
            index_row("추자도", 33.95, 126.30, "2099-01-02", "일", "돌돔", "매우좋음"),
            index_row("가거도", 34.07, 125.08, "2099-01-01", "일", "감성돔", "좋음"),
        ]
        morning = fishing_index.today_grades(rows, datetime(2099, 1, 1, 9, tzinfo=providers.SEOUL))
        self.assertEqual(morning["추자도"], {"grade": "매우좋음", "session": "오전"})
        afternoon = fishing_index.today_grades(rows, datetime(2099, 1, 1, 15, tzinfo=providers.SEOUL))
        self.assertEqual(afternoon["추자도"], {"grade": "보통", "session": "오후"})
        self.assertEqual(afternoon["가거도"], {"grade": "좋음", "session": "일"})
        self.assertEqual(fishing_index.today_grades(rows, datetime(2099, 1, 5, 9, tzinfo=providers.SEOUL)), {})

    @patch.dict(os.environ, {'KMA_API_HUB_KEY': 'k', 'KHOA_TIDEBED_API_KEY': 't', 'KHOA_FISHING_API_KEY': 'f'})
    def test_points_include_today_grade(self):
        service.cache.clear()
        with patch.object(providers, '_fetch', side_effect=fake_fetch([])), \
             patch.object(service, 'datetime', Mock(now=Mock(return_value=service.datetime(2099, 1, 1, 9, tzinfo=providers.SEOUL)))):
            points = {p['id']: p for p in service.get_fishing_points()}
        self.assertEqual((points['추자도']['today_grade'], points['추자도']['today_session']), ('매우좋음', '오전'))
        self.assertEqual(points['가거도']['today_grade'], '보통')


@patch.dict(os.environ, {'KMA_API_HUB_KEY': 'k', 'KHOA_TIDEBED_API_KEY': 't', 'KHOA_FISHING_API_KEY': 'f'})
class PointIndexApiTests(unittest.TestCase):
    def setUp(self):
        service.cache.clear()

    def test_point_index_returns_only_that_point_without_other_agencies(self):
        calls = []
        client = TestClient(app)
        with patch.object(providers, '_fetch', side_effect=fake_fetch(calls)):
            response = client.get('/api/fishing/points/추자도/index')
            missing = client.get('/api/fishing/points/없는곳/index')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body['point']['name'], body['point']['distance_km'], body['point_id']), ('추자도', 0.0, '추자도'))
        self.assertEqual([d['date'] for d in body['days']], ['2099-01-01', '2099-01-02'])
        self.assertTrue(all('fcstFishing' in url for url in calls))  # 해상예보·조위 등은 부르지 않음
        self.assertEqual(missing.status_code, 404)
