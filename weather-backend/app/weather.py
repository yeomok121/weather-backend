import os
import requests
from app.weather_diagnostics import logged_get, logged_json, log_error, emit
import math
import concurrent.futures
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

KMA_API_KEY = os.getenv("KMA_API_KEY")
OWM_API_KEY = os.getenv("OPENWEATHER_API_KEY")
MOUNTAIN_API_KEY = os.getenv("MOUNTAIN_API_KEY")
BASE = "https://api.openweathermap.org/data/2.5"

def get_nx_ny(lat, lon):
    RE = 6371.00877; GRID = 5.0; SLAT1 = 30.0; SLAT2 = 60.0; OLON = 126.0; OLAT = 38.0; XO = 43; YO = 136
    DEGRAD = math.pi / 180.0
    re = RE / GRID
    slat1 = SLAT1 * DEGRAD; slat2 = SLAT2 * DEGRAD; olon = OLON * DEGRAD; olat = OLAT * DEGRAD
    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = math.pow(sf, sn) * math.cos(slat1) / sn
    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re * sf / math.pow(ro, sn)
    ra = math.tan(math.pi * 0.25 + lat * DEGRAD * 0.5)
    ra = re * sf / math.pow(ra, sn)
    theta = lon * DEGRAD - olon
    if theta > math.pi: theta -= 2.0 * math.pi
    if theta < -math.pi: theta += 2.0 * math.pi
    theta *= sn
    nx = math.floor(ra * math.sin(theta) + XO + 0.5)
    ny = math.floor(ro - ra * math.cos(theta) + YO + 0.5)
    return nx, ny

def get_kma_base_time():
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst) - timedelta(hours=1)
    date_str = now.strftime("%Y%m%d")
    hour = now.hour
    if hour < 2:
        date_str = (now - timedelta(days=1)).strftime("%Y%m%d")
        time_str = "2300"
    elif hour < 5: time_str = "0200"
    elif hour < 8: time_str = "0500"
    elif hour < 11: time_str = "0800"
    elif hour < 14: time_str = "1100"
    elif hour < 17: time_str = "1400"
    elif hour < 20: time_str = "1700"
    elif hour < 23: time_str = "2000"
    else: time_str = "2300"
    return date_str, time_str

def get_yesterday_temp(lat: float, lon: float, nx: int, ny: int) -> float | None:
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst)
    yesterday = now - timedelta(days=1)
    y_date_str = yesterday.strftime("%Y%m%d")
    target_hour_str = now.strftime("%H00")

    try:
        url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
        params = {
            "serviceKey": KMA_API_KEY,
            "pageNo": "1", "numOfRows": "300", "dataType": "JSON",
            "base_date": y_date_str, "base_time": "0200", "nx": nx, "ny": ny
        }
        res = logged_get(url, params=params, timeout=3)
        items = logged_json(res).get("response", {}).get("body", {}).get("items", {}).get("item", [])
        for item in items:
            if item.get("fcstDate") == y_date_str and item.get("fcstTime") == target_hour_str and item.get("category") == "TMP":
                return float(item.get("fcstValue"))
    except Exception as diagnostic_exc:
        log_error("weather_fallback", diagnostic_exc, function="get_yesterday_temp")
        pass

    try:
        om_url = "https://api.open-meteo.com/v1/forecast"
        om_params = {
            "latitude": lat, "longitude": lon,
            "hourly": "temperature_2m",
            "past_days": 1, "forecast_days": 1,
            "timezone": "Asia/Seoul"
        }
        om_res = logged_get(om_url, params=om_params, timeout=3)
        if om_res.status_code == 200:
            hourly = logged_json(om_res).get("hourly", {})
            times = hourly.get("time", [])
            temps = hourly.get("temperature_2m", [])
            target_iso = yesterday.strftime("%Y-%m-%dT%H:00")
            if target_iso in times:
                idx = times.index(target_iso)
                if idx < len(temps) and temps[idx] is not None:
                    return float(temps[idx])
    except Exception as diagnostic_exc:
        log_error("weather_fallback", diagnostic_exc, function="get_yesterday_temp")
        pass
    return None

def get_current_weather(lat: float, lon: float):
    nx, ny = get_nx_ny(lat, lon)
    base_date, base_time = get_kma_base_time()
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst)
    today = now.strftime("%Y%m%d")
    current_hour = now.strftime("%Y%m%d %H00")
    params = {"serviceKey": KMA_API_KEY, "pageNo": "1", "numOfRows": "350", "dataType": "JSON",
              "base_date": base_date, "base_time": base_time, "nx": nx, "ny": ny}
    try:
        response = logged_get("http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst", params=params, timeout=5)
        items = logged_json(response).get("response", {}).get("body", {}).get("items", {}).get("item", [])
    except Exception as exc:
        log_error("weather_fallback", exc, function="get_current_weather")
        items = []

    slots = {}
    today_temps, maxima, minima = [], [], []
    for item in items:
        date, hour, category = item.get("fcstDate", ""), item.get("fcstTime", ""), item.get("category")
        value = item.get("fcstValue")
        if f"{date} {hour}" >= current_hour:
            slots.setdefault(f"{date} {hour}", {})[category] = value
        if date == today:
            try:
                if category == "TMP": today_temps.append(float(value))
                elif category == "TMX": maxima.append(float(value))
                elif category == "TMN": minima.append(float(value))
            except (TypeError, ValueError):
                pass
    target = slots[min(slots)] if slots else {}
    def numeric(value):
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    owm = {}
    try:
        response = logged_get(f"{BASE}/weather", params={"lat": lat, "lon": lon, "appid": OWM_API_KEY, "units": "metric", "lang": "kr"}, timeout=5)
        if response.status_code == 200: owm = logged_json(response)
    except Exception as exc:
        log_error("weather_fallback", exc, function="get_current_weather")
    main = owm.get("main", {})
    kma_temp = numeric(target.get("TMP"))
    temp = kma_temp if kma_temp is not None else numeric(main.get("temp"))
    if temp is None:
        raise RuntimeError("기온 자료를 받지 못했습니다. 기상 공급 기관 연결을 다시 확인해 주세요.")
    humidity = numeric(target.get("REH"))
    if humidity is None: humidity = numeric(main.get("humidity"))
    wind = numeric(target.get("WSD"))
    if wind is None: wind = numeric(owm.get("wind", {}).get("speed"))
    feels_like = numeric(main.get("feels_like"))
    temp_max = max(maxima or today_temps) if maxima or today_temps else numeric(main.get("temp_max"))
    temp_min = min(minima or today_temps) if minima or today_temps else numeric(main.get("temp_min"))
    if temp_max is not None: temp_max = round(max(temp, temp_max))
    if temp_min is not None: temp_min = round(min(temp, temp_min))
    sys_data = owm.get("sys", {})
    sunrise, sunset = None, None
    is_day = 6 <= now.hour < 18
    if sys_data.get("sunrise") and sys_data.get("sunset"):
        sunrise = datetime.fromtimestamp(sys_data["sunrise"], kst).strftime("%H:%M")
        sunset = datetime.fromtimestamp(sys_data["sunset"], kst).strftime("%H:%M")
        is_day = sys_data["sunrise"] <= now.timestamp() <= sys_data["sunset"]
    suffix = "d" if is_day else "n"
    weather = (owm.get("weather") or [{}])[0]
    description, icon = weather.get("description", "하늘 상태 자료 없음"), weather.get("icon")
    pty, sky = str(target.get("PTY", "")), str(target.get("SKY", ""))
    if pty in {"1", "4"} or (pty == "2" and temp > 0):
        description, icon = ("눈", f"13{suffix}") if temp <= 0 else ("비", f"09{suffix}")
    elif pty in {"2", "3"} and temp <= 0: description, icon = "눈", f"13{suffix}"
    elif sky == "3": description, icon = "구름많음", f"03{suffix}"
    elif sky == "4": description, icon = "흐림", f"04{suffix}"
    elif sky == "1": description, icon = "맑음", f"01{suffix}"
    yesterday = get_yesterday_temp(lat, lon, nx, ny)
    return {
        "city": "기상청 동네예보" if kma_temp is not None else "OpenWeather 현재 날씨",
        "temp": round(temp), "feels_like": round(feels_like) if feels_like is not None else None,
        "temp_max": temp_max, "temp_min": temp_min,
        "temp_diff": round(temp - yesterday) if yesterday is not None else None,
        "humidity": round(humidity) if humidity is not None else None,
        "wind_speed": wind, "description": description, "icon": icon,
        "sunrise": sunrise, "sunset": sunset,
        "weather_sources": {
            "temperature": "기상청 동네예보" if kma_temp is not None else "OpenWeather 현재 날씨 (대체 자료)",
            "wind": "기상청 동네예보" if numeric(target.get("WSD")) is not None else "OpenWeather 또는 자료 없음",
            "humidity": "기상청 동네예보" if numeric(target.get("REH")) is not None else "OpenWeather 또는 자료 없음",
        },
        "data_as_of": now.isoformat(timespec="minutes"),
    }


def get_hourly_forecast(lat: float, lon: float, count: int = 24):
    nx, ny = get_nx_ny(lat, lon)
    base_date, base_time = get_kma_base_time()
    
    url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    params = {
        "serviceKey": KMA_API_KEY,
        "pageNo": "1", "numOfRows": "1000", "dataType": "JSON",
        "base_date": base_date, "base_time": base_time, "nx": nx, "ny": ny
    }
    
    try:
        res = logged_get(url, params=params, timeout=5)
        items = logged_json(res).get("response", {}).get("body", {}).get("items", {}).get("item", [])
    except Exception as diagnostic_exc:
        log_error("weather_fallback", diagnostic_exc, function="get_hourly_forecast")
        items = []

    kst = timezone(timedelta(hours=9))
    now_str = datetime.now(kst).strftime("%Y%m%d %H00")

    forecast_dict = {}
    for item in items:
        ftime = f"{item['fcstDate']} {item['fcstTime']}"
        if ftime < now_str: continue
            
        if ftime not in forecast_dict:
            forecast_dict[ftime] = {
                "temp": None, "sky": None, "pty": None, "pop": 0.0,
                "wind_speed": None, "humidity": None,
            }
        
        cat = item["category"]
        val = item["fcstValue"]
        if cat == "TMP": forecast_dict[ftime]["temp"] = float(val)
        elif cat == "SKY": forecast_dict[ftime]["sky"] = str(val)
        elif cat == "PTY": forecast_dict[ftime]["pty"] = str(val)
        elif cat == "POP": forecast_dict[ftime]["pop"] = float(val) / 100.0
        elif cat == "WSD": forecast_dict[ftime]["wind_speed"] = float(val)
        elif cat == "REH": forecast_dict[ftime]["humidity"] = float(val)
        
    result = []
    sorted_times = sorted(forecast_dict.keys())[:count]
    
    for ftime in sorted_times:
        f_data = forecast_dict[ftime]
        pty = f_data["pty"]; sky = f_data["sky"]; t = f_data["temp"]
        if t is None:
            continue
        hour_num = int(ftime[9:11])
        
        is_day = 6 <= hour_num < 18
        suffix = "d" if is_day else "n"
        
        # 💡 [핵심 수정] 기온이 0도 이하인데 강수 확률/형태가 있으면 눈으로 변환
        desc = "맑음"; icon = f"01{suffix}"
        if pty in ["1", "4"] or (f_data["pop"] >= 0.3 and t > 0): 
            if t <= 0:
                desc = "눈"; icon = f"13{suffix}"
            else:
                desc = "비"; icon = f"09{suffix}"
        elif (pty in ["2", "3"] or f_data["pop"] >= 0.3) and t <= 0: 
            desc = "눈"; icon = f"13{suffix}"
        elif sky == "3": 
            desc = "구름많음"; icon = f"03{suffix}"
        elif sky == "4": 
            desc = "흐림"; icon = f"04{suffix}"
        
        result.append({
            "time": f"{hour_num}시", "temp": round(t),
            "valid_at": datetime.strptime(ftime, "%Y%m%d %H%M").replace(tzinfo=kst).isoformat(),
            "pop": f_data["pop"], "description": desc, "icon": icon,
            "wind_speed": round(f_data["wind_speed"], 1) if f_data["wind_speed"] is not None else None,
            "humidity": round(f_data["humidity"]) if f_data["humidity"] is not None else None,
        })
    return result

def get_weekly_forecast(lat: float, lon: float):
    try:
        res = logged_get(
            f"{BASE}/forecast",
            params={"lat": lat, "lon": lon, "appid": OWM_API_KEY, "units": "metric", "lang": "kr"},
            timeout=5
        )
        if res.status_code != 200: return []
        data = logged_json(res)
        timezone_offset = int(data.get("city", {}).get("timezone", 9 * 60 * 60))
        local_timezone = timezone(timedelta(seconds=timezone_offset))
        
        daily_data = {}
        for item in data.get("list", []):
            dt_txt = item.get("dt_txt", "")
            if not dt_txt: continue
            
            forecast_timestamp = item.get("dt")
            if forecast_timestamp is not None:
                local_forecast_time = datetime.fromtimestamp(forecast_timestamp, local_timezone)
            else:
                utc_forecast_time = datetime.strptime(dt_txt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                local_forecast_time = utc_forecast_time.astimezone(local_timezone)
            date_str = local_forecast_time.strftime("%Y-%m-%d")
            main_data = item.get("main", {})
            temp = main_data.get("temp")
            pop = item.get("pop")
            weather_list = item.get("weather", [])
            icon = weather_list[0].get("icon", "01d") if weather_list else "01d"
            
            wind_speed = item.get("wind", {}).get("speed")
            humidity = main_data.get("humidity")
            
            if date_str not in daily_data:
                daily_data[date_str] = {"temps": [], "pops": [], "icons": [], "winds": [], "hums": []}
            if temp is not None: daily_data[date_str]["temps"].append(temp)
            if pop is not None: daily_data[date_str]["pops"].append(pop)
            daily_data[date_str]["icons"].append(icon)
            if wind_speed is not None: daily_data[date_str]["winds"].append(wind_speed)
            if humidity is not None: daily_data[date_str]["hums"].append(humidity)
        
        result = []
        today_str = datetime.now(local_timezone).strftime("%Y-%m-%d")
        days_kr = ["월", "화", "수", "목", "금", "토", "일"]
        
        for d_str, d_info in daily_data.items():
            try:
                d_obj = datetime.strptime(d_str, "%Y-%m-%d")
                weekday = days_kr[d_obj.weekday()]
            except Exception as diagnostic_exc:
                log_error("weather_fallback", diagnostic_exc, function="get_weekly_forecast")
                continue
            
            icons = d_info["icons"]
            day_icons = [ic for ic in icons if "d" in ic]
            rep_icon = day_icons[len(day_icons)//2] if day_icons else (icons[0] if icons else "01d")
            
            temps = d_info["temps"]
            pops = d_info["pops"]
            winds = d_info["winds"]
            hums = d_info["hums"]
            
            if not temps or not pops: continue
            
            avg_wind = sum(winds) / len(winds) if winds else None
            avg_hum = sum(hums) / len(hums) if hums else None
            
            max_t = round(max(temps))
            min_t = round(min(temps))
            max_pop = max(pops)
            
            # 💡 [핵심 수정] 주간 예보에서도 영하의 날씨에 강수 확률이 30% 이상이면 비 아이콘(09, 10, 11)을 눈(13)으로 강제 변환
            if max_pop >= 0.3 and max_t <= 0:
                if rep_icon.startswith("09") or rep_icon.startswith("10") or rep_icon.startswith("11"):
                    rep_icon = f"13{rep_icon[-1]}"
            
            result.append({
                "date": f"{d_obj.month}/{d_obj.day}",
                "full_date": d_str,
                "day": weekday,
                "isToday": d_str == today_str,
                "temp_max": max_t,
                "temp_min": min_t,
                "pop": round(max_pop * 100),
                "icon": rep_icon,
                "wind_speed": round(avg_wind, 1) if avg_wind is not None else None,
                "humidity": round(avg_hum) if avg_hum is not None else None
            })
        return result[:5]
    except Exception as diagnostic_exc:
        log_error("weather_fallback", diagnostic_exc, function="get_weekly_forecast")
        return []

def search_location(q: str):
    try:
        res = logged_get(
            "https://api.openweathermap.org/geo/1.0/direct",
            params={"q": q, "limit": 1, "appid": OWM_API_KEY}, timeout=5)
        if res.status_code != 200: return None
        data = logged_json(res)
        if not isinstance(data, list) or len(data) == 0: return None
        local_names = data[0].get("local_names", {})
        kor_name = local_names.get("ko", data[0].get("name", q))
        return {"name": kor_name, "lat": data[0].get("lat", 0), "lon": data[0].get("lon", 0)}
    except Exception as diagnostic_exc:
        log_error("weather_fallback", diagnostic_exc, function="search_location")
        return None

def get_air_quality(lat: float, lon: float):
    try:
        res = logged_get(
            f"{BASE}/air_pollution",
            params={"lat": lat, "lon": lon, "appid": OWM_API_KEY}, timeout=5)
        if res.status_code != 200: return {"pm25": None, "pm10": None, "level": "점검중"}
        data = logged_json(res)
        if "list" not in data or len(data["list"]) == 0: return {"pm25": None, "pm10": None, "level": "점검중"}
        item = data["list"][0]
        components = item.get("components", {})
        pm25 = components.get("pm2_5")
        pm10 = components.get("pm10")
        if pm25 is None:
            return {"pm25": None, "pm10": round(pm10) if pm10 is not None else None, "level": "점검중"}
        if pm25 <= 15: level = "좋음"
        elif pm25 <= 35: level = "보통"
        elif pm25 <= 75: level = "나쁨"
        else: level = "매우나쁨"
        return {"pm25": round(pm25), "pm10": round(pm10) if pm10 is not None else None, "level": level}
    except Exception as diagnostic_exc:
        log_error("weather_fallback", diagnostic_exc, function="get_air_quality")
        return {"pm25": None, "pm10": None, "level": "점검중"}

MOUNTAINS_DB = {
    "북한산": {
        "lat": 37.6620, "lon": 126.9930, "elevation": 836, "peak": "백운대",
        "terrain": "화강암 암릉", "difficulty": "중상", "region": "서울/경기",
        "course_tip": "백운대 정상 부근 화강암 바위가 가파르고 미끄러우니 접지력이 좋은 등산화와 장갑을 꼭 착용하세요.",
        "cctv_url": "https://www.knps.or.kr/portal/main/contents.do?menuNo=8000159"
    },
    "설악산": {
        "lat": 38.1195, "lon": 128.4655, "elevation": 1708, "peak": "대청봉",
        "terrain": "고산 암릉·돌계단", "difficulty": "상", "region": "강원 인제/속초",
        "course_tip": "오색~대청봉 코스는 경사가 급하고 정상 능선에 강한 칼바람이 불어 사계절 방풍 재킷이 필수입니다.",
        "cctv_url": "https://www.knps.or.kr/portal/main/contents.do?menuNo=8000159"
    },
    "한라산": {
        "lat": 33.3615, "lon": 126.5292, "elevation": 1947, "peak": "백록담",
        "terrain": "고산 화산지형·현무암", "difficulty": "상", "region": "제주",
        "course_tip": "성판악·관음사 코스는 사전 탐방 예약이 필수이며, 정상 백록담은 평지보다 12°C 이상 낮고 기상 변화가 급격합니다.",
        "cctv_url": "https://www.jeju.go.kr/hallasan/info/cctv.htm"
    },
    "지리산": {
        "lat": 35.3369, "lon": 127.7306, "elevation": 1915, "peak": "천왕봉",
        "terrain": "고산 능선·너덜길", "difficulty": "상", "region": "경남/전남/전북",
        "course_tip": "중산리~천왕봉 코스는 장거리 산행이므로 일몰 시간과 체력을 고려해 헤드랜턴과 행동식을 충분히 준비하세요.",
        "cctv_url": "https://www.knps.or.kr/portal/main/contents.do?menuNo=8000159"
    },
    "덕유산": {
        "lat": 35.8596, "lon": 127.7468, "elevation": 1614, "peak": "향적봉",
        "terrain": "아고산대 능선·주목군락", "difficulty": "중하", "region": "전북 무주",
        "course_tip": "무주리조트 곤돌라로 설천봉까지 쉽게 오를 수 있으나, 향적봉 정상 능선은 습도가 높고 상고대·강풍이 잦아 보온에 유의하세요.",
        "cctv_url": "https://www.knps.or.kr/portal/main/contents.do?menuNo=8000159"
    },
    "소백산": {
        "lat": 36.9575, "lon": 128.4847, "elevation": 1439, "peak": "비로봉",
        "terrain": "초원 능선(육산)", "difficulty": "중", "region": "충북 단양/경북 영주",
        "course_tip": "나무가 없는 탁 트인 능선이라 바람을 막아줄 지형물이 없습니다. 체감온도가 급락하니 방풍·방한모를 꼭 챙기세요.",
        "cctv_url": "https://www.knps.or.kr/portal/main/contents.do?menuNo=8000159"
    },
    "태백산": {
        "lat": 37.0964, "lon": 128.9163, "elevation": 1567, "peak": "장군봉(천제단)",
        "terrain": "완만한 육산·주목군락", "difficulty": "중하", "region": "강원 태백",
        "course_tip": "유일사·당골 코스는 해발고도에 비해 경사가 완만해 초보자도 오르기 좋으며, 겨울철 눈꽃과 일출 조망이 뛰어납니다.",
        "cctv_url": "https://www.knps.or.kr/portal/main/contents.do?menuNo=8000159"
    },
    "오대산": {
        "lat": 37.7946, "lon": 128.5432, "elevation": 1563, "peak": "비로봉",
        "terrain": "부드러운 흙길(육산)", "difficulty": "중", "region": "강원 평창/홍천",
        "course_tip": "상원사~비로봉 구간은 흙길과 계단이 잘 정비되어 있으나 정상 직전 오르막이 가파르니 등산 스틱을 활용하세요.",
        "cctv_url": "https://www.knps.or.kr/portal/main/contents.do?menuNo=8000159"
    },
    "치악산": {
        "lat": 37.3652, "lon": 128.0558, "elevation": 1288, "peak": "비로봉",
        "terrain": "급경사 계단·너덜길", "difficulty": "상", "region": "강원 원주",
        "course_tip": "구룡사~사다리병창 코스는 악명 높은 급경사 철계단과 돌길이 이어지므로 무릎 보호대와 스틱이 필수입니다.",
        "cctv_url": "https://www.knps.or.kr/portal/main/contents.do?menuNo=8000159"
    },
    "월악산": {
        "lat": 36.8856, "lon": 128.1058, "elevation": 1097, "peak": "영봉",
        "terrain": "험준한 암릉·철계단", "difficulty": "상", "region": "충북 제천",
        "course_tip": "영봉 직전 수백 개의 가파른 철계단 구간이 있으며 충주호에서 불어오는 골바람이 강해 미끄럼과 강풍에 주의해야 합니다.",
        "cctv_url": "https://www.knps.or.kr/portal/main/contents.do?menuNo=8000159"
    },
    "속리산": {
        "lat": 36.5430, "lon": 127.8712, "elevation": 1058, "peak": "천왕봉(문장대)",
        "terrain": "기암괴석·암릉 능선", "difficulty": "중", "region": "충북 보은/경북 상주",
        "course_tip": "문장대 바위 정상은 사방이 트여 조망이 압도적이지만 난간 구간에서 강풍이 불 때 안전에 각별히 유의하세요.",
        "cctv_url": "https://www.knps.or.kr/portal/main/contents.do?menuNo=8000159"
    },
    "가야산": {
        "lat": 35.8225, "lon": 128.1189, "elevation": 1430, "peak": "상왕봉(우두봉)",
        "terrain": "뾰족한 암봉·돌계단", "difficulty": "중상", "region": "경남 합천/경북 성주",
        "course_tip": "해인사·백운동 코스 모두 정상 부근 암릉 구간이 험준하므로 비나 눈이 온 뒤에는 바위 미끄럼을 특히 조심하세요.",
        "cctv_url": "https://www.knps.or.kr/portal/main/contents.do?menuNo=8000159"
    },
    "계룡산": {
        "lat": 36.3533, "lon": 127.2144, "elevation": 845, "peak": "관음봉",
        "terrain": "암릉 능선·가파른 계단", "difficulty": "중상", "region": "충남 공주/계룡",
        "course_tip": "동학사~남매탑~관음봉 코스는 해발고도는 낮아도 자연성릉 바위 능선과 고깔바위 계단이 가팔라 체력 소모가 큽니다.",
        "cctv_url": "https://www.knps.or.kr/portal/main/contents.do?menuNo=8000159"
    },
    "무등산": {
        "lat": 35.1342, "lon": 126.9886, "elevation": 1187, "peak": "서석대·입석대",
        "terrain": "주상절리대·완만한 능선", "difficulty": "중", "region": "광주/전남 화순",
        "course_tip": "증심사·원효사 코스는 완만하나 서석대 주상절리 능선 구간은 그늘이 적고 바람이 강해 자외선 차단과 방풍 준비가 필요합니다.",
        "cctv_url": "https://www.knps.or.kr/portal/main/contents.do?menuNo=8000159"
    },
    "주왕산": {
        "lat": 36.3894, "lon": 129.1628, "elevation": 721, "peak": "주봉",
        "terrain": "기암절벽 협곡·흙길", "difficulty": "하", "region": "경북 청송",
        "course_tip": "대전사~주봉~용추폭포 코스는 험한 구간이 적어 초보자 산행에 적합하며, 협곡 내 습도가 높아 아침 물안개가 자주 낍니다.",
        "cctv_url": "https://www.knps.or.kr/portal/main/contents.do?menuNo=8000159"
    },
    "도봉산": {
        "lat": 37.6987, "lon": 127.0155, "elevation": 740, "peak": "자운봉(신선대)",
        "terrain": "거대 화강암 암봉", "difficulty": "중상", "region": "서울 도봉/경기 양주",
        "course_tip": "신선대 정상 오름길은 가파른 바위와 쇠난간을 잡고 올라야 하므로 반장갑과 릿지화(등산화) 착용을 강력히 권장합니다.",
        "cctv_url": "https://www.knps.or.kr/portal/main/contents.do?menuNo=8000159"
    },
    "관악산": {
        "lat": 37.4445, "lon": 126.9639, "elevation": 632, "peak": "연주대",
        "terrain": "바위 능선·돌계단", "difficulty": "중", "region": "서울 관악/경기 과천",
        "course_tip": "사당·서울대 코스는 바위가 많고 노출된 암릉 구간이 이어져 비나 서리가 내린 날에는 미끄럼 사고에 주의해야 합니다.",
        "cctv_url": "https://www.youtube.com/results?search_query=%EA%B4%80%EC%95%85%EC%82%B0+%EC%97%B0%EC%A3%BC%EB%8C%80+%EC%8B%A4%EC%8B%9C%EA%B0%84"
    },
    "대둔산": {
        "lat": 36.1247, "lon": 127.3211, "elevation": 878, "peak": "마천대",
        "terrain": "기암절벽·구름다리·철계단", "difficulty": "중", "region": "충남 논산/전북 완주",
        "course_tip": "금강구름다리와 삼선계단은 경사가 매우 가파르고 일방통행이므로 고소공포증이나 강풍 시 우회 등산로를 이용하세요.",
        "cctv_url": "https://www.youtube.com/results?search_query=%EB%8C%80%EB%91%94%EC%82%B0+%EB%A7%88%EC%B2%9C%EB%8C%80+%EC%8B%A4%EC%8B%9C%EA%B0%84"
    },
    "내장산": {
        "lat": 35.4783, "lon": 126.8892, "elevation": 763, "peak": "신선봉",
        "terrain": "단풍 계곡·암릉 능선", "difficulty": "중", "region": "전북 정읍/순창",
        "course_tip": "일주문~내장사 구간은 평탄하지만 신선봉 정상으로 오르는 능선은 경사가 급하고 습기가 많아 미끄럼에 유의하세요.",
        "cctv_url": "https://www.knps.or.kr/portal/main/contents.do?menuNo=8000159"
    },
    "마이산": {
        "lat": 35.7611, "lon": 127.4122, "elevation": 686, "peak": "암마이봉",
        "terrain": "역암 바위·데크 계단", "difficulty": "중하", "region": "전북 진안",
        "course_tip": "암마이봉 구간은 겨울철 결빙 시 통제될 수 있으니 노면 상태를 꼭 확인하세요.",
        "cctv_url": "https://www.youtube.com/results?search_query=%EB%A7%88%EC%9D%B4%EC%82%B0+%EC%8B%A4%EC%8B%9C%EA%B0%84"
    }
}

def search_mountain(q: str):
    q_clean = q.strip()
    for name, data in MOUNTAINS_DB.items():
        if q_clean in name or name in q_clean or q_clean in data.get("peak", ""):
            return {
                "name": name,
                "lat": data["lat"],
                "lon": data["lon"],
                "elevation": data["elevation"],
                "peak": data["peak"],
                "terrain": data["terrain"],
                "difficulty": data["difficulty"],
                "region": data["region"],
                "course_tip": data["course_tip"],
                "cctv_url": data.get("cctv_url", "https://www.knps.or.kr/portal/main/contents.do?menuNo=8000159")
            }
    return None

def apply_mountain_lapse_rate(temp: float, elevation: int) -> float:
    drop = ((max(50, elevation) - 50) / 100.0) * 0.65
    return temp - drop

def calculate_safe_descent_time(sunset_str: str) -> str:
    try:
        h, m = map(int, sunset_str.split(":"))
        total_min = max(0, (h * 60 + m) - 120)
        return f"{total_min // 60:02d}:{total_min % 60:02d}"
    except Exception:
        return "15:30"

def fetch_forest_service_api(mountain_name: str) -> dict | None:
    if not MOUNTAIN_API_KEY: return None
    try:
        url = "http://apis.data.go.kr/1400377/mtweather/mountListSearch"
        params = { "serviceKey": MOUNTAIN_API_KEY, "pageNo": "1", "numOfRows": "10", "_type": "json", "searchWrd": mountain_name }
        res = requests.get(url, params=params, timeout=2.5)
        if res.status_code == 200:
            items = res.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
            if isinstance(items, dict): items = [items]
            if items and len(items) > 0:
                first = items[0]
                obs_temp = first.get("tm2m") or first.get("temp")
                obs_wind = first.get("ws2m") or first.get("wind")
                obs_hum = first.get("hm2m") or first.get("reh")
                if obs_temp is not None:
                    return {
                        "temp": float(obs_temp),
                        "wind_speed": float(obs_wind) if obs_wind is not None else None,
                        "humidity": int(float(obs_hum)) if obs_hum is not None else None
                    }
    except Exception: pass
    return None

def get_mountain_weather(mountain_name: str):
    m_info = MOUNTAINS_DB.get(mountain_name)
    if not m_info:
        found = search_mountain(mountain_name)
        if not found: return None
        mountain_name = found["name"]
        m_info = MOUNTAINS_DB[mountain_name]

    lat, lon, elevation = m_info["lat"], m_info["lon"], m_info["elevation"]

    with concurrent.futures.ThreadPoolExecutor() as executor:
        f_base = executor.submit(get_current_weather, lat, lon)
        f_hourly = executor.submit(get_hourly_forecast, lat, lon, 48)
        f_aqi = executor.submit(get_air_quality, lat, lon)
        f_weekly = executor.submit(get_weekly_forecast, lat, lon)
        f_forest = executor.submit(fetch_forest_service_api, mountain_name)

        base_weather = f_base.result()
        base_hourly = f_hourly.result()
        air_quality = f_aqi.result()
        base_weekly = f_weekly.result()
        forest_obs = f_forest.result()

    base_temp = round(base_weather["temp"])
    summit_temp = round(apply_mountain_lapse_rate(base_weather["temp"], elevation))
    base_wind = base_weather.get("wind_speed")
    summit_wind = round(max(1.2, base_wind * 1.6 + (elevation / 1000.0)), 1) if base_wind is not None else None
    base_humidity = base_weather.get("humidity")
    summit_humidity = min(100, int(base_humidity + 8)) if base_humidity is not None else None

    # 산림청 관측값은 항목별로 존재하는 경우에만 해당 추정값을 대체합니다.
    source_as_of = datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="minutes")
    weather_sources = {
        "temperature": "고도 보정 추정값",
        "wind": "고도·지형 보정 추정값",
        "humidity": "고도 보정 추정값",
    }
    if forest_obs:
        if forest_obs.get("temp") is not None:
            summit_temp = round(forest_obs["temp"])
            weather_sources["temperature"] = "산림청 산악기상 관측 응답값"
        if forest_obs.get("wind_speed") is not None:
            summit_wind = round(forest_obs["wind_speed"], 1)
            weather_sources["wind"] = "산림청 산악기상 관측 응답값"
        if forest_obs.get("humidity") is not None:
            summit_humidity = forest_obs["humidity"]
            weather_sources["humidity"] = "산림청 산악기상 관측 응답값"

    wind_chill_drop = round(summit_wind * 0.7) if summit_temp <= 15 and summit_wind is not None else 0
    summit_feels_like = summit_temp - wind_chill_drop
    summit_drop = max(1, base_temp - summit_temp)
    safe_descent_time = calculate_safe_descent_time(base_weather["sunset"]) if base_weather.get("sunset") else None

    mountain_hourly = []
    for h in base_hourly:
        m_h_temp = apply_mountain_lapse_rate(h["temp"], elevation)
        h_wind = h.get("wind_speed")
        m_h_wind = round(max(1.2, h_wind * 1.6 + (elevation / 1000.0)), 1) if h_wind is not None else None
        h_humidity = h.get("humidity")
        m_h_humidity = min(100, int(h_humidity + 8)) if h_humidity is not None else None
        
        # 💡 [핵심 수정] 산악 시간대별 예보에서도 영하+강수확률 30% 이상이면 비를 눈으로 변환
        m_icon = h["icon"]
        if h["pop"] >= 0.3 and m_h_temp <= 0:
            if m_icon.startswith("09") or m_icon.startswith("10") or m_icon.startswith("11"):
                m_icon = f"13{m_icon[-1]}"
                
        mountain_hourly.append({
            "time": h["time"], "temp": round(m_h_temp), "pop": h["pop"],
            "valid_at": h.get("valid_at"),
            "description": "눈" if m_icon.startswith("13") else h["description"],
            "icon": m_icon,
            "wind_speed": m_h_wind,
            "humidity": m_h_humidity,
        })

    mountain_weekly = []
    for w in base_weekly:
        w_temp_max = round(apply_mountain_lapse_rate(w["temp_max"], elevation))
        w_temp_min = round(apply_mountain_lapse_rate(w["temp_min"], elevation))
        w_wind = round(max(1.2, w["wind_speed"] * 1.6 + (elevation / 1000.0)), 1)
        w_hum = min(100, int(w["humidity"] + 8))
        
        # 💡 [핵심 수정] 산악 주간 예보에서도 영하+강수확률 30% 이상이면 비를 눈으로 변환
        m_w_icon = w["icon"]
        if w["pop"] >= 30 and w_temp_max <= 0:
            if m_w_icon.startswith("09") or m_w_icon.startswith("10") or m_w_icon.startswith("11"):
                m_w_icon = f"13{m_w_icon[-1]}"
                
        mountain_weekly.append({
            **w,
            "temp_max": w_temp_max,
            "temp_min": w_temp_min,
            "wind_speed": w_wind,
            "humidity": w_hum,
            "icon": m_w_icon
        })

    return {
        "mountain_name": mountain_name,
        "elevation": elevation,
        "mountain_info": {
            "name": mountain_name,
            "lat": m_info["lat"], "lon": m_info["lon"],
            "elevation": elevation,
            "peak": m_info["peak"],
            "terrain": m_info["terrain"],
            "difficulty": m_info["difficulty"],
            "region": m_info["region"],
            "course_tip": m_info["course_tip"],
            "cctv_url": m_info.get("cctv_url", "https://www.knps.or.kr/portal/main/contents.do?menuNo=8000159"),
            "base_temp": base_temp,
            "summit_drop": summit_drop,
            "safe_descent_time": safe_descent_time,
            "safe_descent_time_label": "권장 하산 시작 시각(일몰 2시간 전 기준)",
            "weather_sources": weather_sources,
            "data_as_of": source_as_of,
            "data_note": "정상 관측값이 없는 항목은 평지 예보와 고도 보정으로 추정합니다."
        },
        "current": {
            "city": f"{mountain_name} ({m_info['peak']})",
            "temp": summit_temp,
            "feels_like": summit_feels_like,
            "base_temp": base_temp,
            "summit_drop": summit_drop,
            "wind_speed": summit_wind,
            "description": base_weather["description"],
            "icon": base_weather["icon"],
            "temp_diff": base_weather.get("temp_diff", 0),
            "humidity": summit_humidity,
            "sunrise": base_weather.get("sunrise", "06:30"),
            "sunset": base_weather.get("sunset", "17:30"),
            "safe_descent_time": safe_descent_time,
            "safe_descent_time_label": "권장 하산 시작 시각(일몰 2시간 전 기준)",
            "weather_sources": weather_sources,
            "data_as_of": source_as_of,
            "data_note": "정상 관측값이 없는 항목은 평지 예보와 고도 보정으로 추정합니다.",
            "temp_max": round(apply_mountain_lapse_rate(base_weather["temp_max"], elevation)) if base_weather.get("temp_max") is not None else None,
            "temp_min": round(apply_mountain_lapse_rate(base_weather["temp_min"], elevation)) if base_weather.get("temp_min") is not None else None
        },
        "hourly": mountain_hourly,
        "air_quality": air_quality,
        "weekly": mountain_weekly
    }
