"""Check public mountain catalog/search/forecast without exposing API keys.

Usage: python scripts/verify_mountain_catalog.py --base-url https://... [--summaries 3]
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def get_json(base, path):
    request = urllib.request.Request(base.rstrip('/') + path, headers={'Accept': 'application/json', 'User-Agent': 'WeatherPlus-Catalog-Verification/1.0'})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def verify(base, summaries):
    errors = []
    status = get_json(base, '/api/mountain/catalog/status')
    rows = get_json(base, '/api/mountain/list')
    if not isinstance(rows, list):
        raise ValueError('산 목록이 배열이 아닙니다')
    names = [item.get('name') for item in rows if isinstance(item, dict)]
    if len(rows) != 50 or len(set(names)) != 50 or not all(names):
        errors.append(f'산 목록은 서로 다른 50개여야 합니다 (현재 {len(rows)}개, 중복 제외 {len(set(names))}개)')
    for row in rows:
        name = row.get('name', '')
        try:
            lat, lon, elevation = float(row['lat']), float(row['lon']), float(row['elevation'])
            if not (33 <= lat <= 39 and 124 <= lon <= 132 and 0 < elevation < 2500):
                raise ValueError('좌표 또는 고도 범위 오류')
            result = get_json(base, '/api/mountain/search?q=' + urllib.parse.quote(name))
            if result.get('name') != name:
                raise ValueError('검색 결과가 선택한 산과 일치하지 않습니다')
        except (KeyError, TypeError, ValueError, urllib.error.URLError, TimeoutError) as exc:
            errors.append(f'{name}: 목록/검색 검증 실패 ({type(exc).__name__})')
    # 기존 산 한 곳과 새 산을 우선해 예보를 확인합니다.
    sample = rows[:1] + rows[20:20 + max(0, summaries - 1)] if summaries else []
    for row in sample:
        name = row.get('name', '')
        try:
            data = get_json(base, '/api/mountain/summary?name=' + urllib.parse.quote(name))
            current = data.get('current') or {}
            if data.get('mountain_name') != name or not isinstance(current.get('temp'), (int, float)) or isinstance(current.get('temp'), bool):
                raise ValueError('산 이름 또는 기온 누락')
            if not isinstance(data.get('hourly'), list):
                raise ValueError('시간별 예보 형식 오류')
            print(f'예보 응답 확인: {name} · 기온 {current["temp"]}°C · 시간별 {len(data["hourly"])}건')
            if not data['hourly']:
                errors.append(f'{name}: 시간별 예보 자료 없음')
        except (KeyError, TypeError, ValueError, urllib.error.URLError, TimeoutError) as exc:
            errors.append(f'{name}: 예보 검증 실패 ({type(exc).__name__})')
    print(f'산 목록 {len(rows)}개 · 검색 확인 {len(rows)}개 · 예보 확인 {len(sample)}개')
    print('기관 API 상태:', status.get('source_check', {}).get('status', 'unknown'))
    for message in errors:
        print('실패:', message)
    return not errors


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Weather+ 산 목록과 예보 응답 확인')
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--summaries', type=int, default=3, choices=range(0, 11), help='실제 예보 확인 개수 (0~10)')
    args = parser.parse_args()
    try:
        sys.exit(0 if verify(args.base_url, args.summaries) else 1)
    except (ValueError, urllib.error.URLError, TimeoutError) as exc:
        print('검증을 완료하지 못했습니다:', type(exc).__name__, file=sys.stderr)
        sys.exit(2)
