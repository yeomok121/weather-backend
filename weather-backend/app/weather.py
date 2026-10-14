import os
import requests
import math
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# 💡 1. 두 개의 API 키를 모두 사용합니다.
KMA_API_KEY = "b82bdd286e9c433b217611deeca7d0a07c8479a8eef0bf54322e76a9db669c12"
OWM_API_KEY = os.getenv("OPENWEATHER_API_KEY")
BASE = "https://api.openweathermap.org/data/2.5"

# [기상청] 위경도 -> 격자 변환 공식
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

# [기상청] 가장 최근 발표 시간 구하기
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

def get_current_weather(lat: float, lon: float):
    nx, ny = get_nx_ny(lat, lon)
    base_date, base_time = get_kma_base_time()
    
    url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    params = {
        "serviceKey": KMA_API_KEY,
        "pageNo": "1", "numOfRows": "50", "dataType": "JSON",
        "base_date": base_date, "base_time": base_time, "nx": nx, "ny": ny
    }
    
    try:
        res = requests.get(url, params=params, timeout=5)
        items = res.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
    except Exception:
        items = []

    temp = 20.0; humidity = 50; wind_speed = 0.0; sky = "1"; pty = "0"
    for item in items:
        cat = item.get("category")
        val = item.get("fcstValue", "0")
        if cat == "TMP": temp = float(val)
        elif cat == "REH": humidity = float(val)
        elif cat == "WSD": wind_speed = float(val)
        elif cat == "SKY": sky = val
        elif cat == "PTY": pty = val

    desc = "맑음"; icon = "01d"
    if pty in ["1", "4"]: desc = "비"; icon = "09d"
    elif pty == "3": desc = "눈"; icon = "13d"
    elif pty == "2": desc = "비/눈"; icon = "13d"
    elif sky == "3": desc = "구름많음"; icon = "03d"
    elif sky == "4": desc = "흐림"; icon = "04d"

    # 💡 프론트엔드가 예상하는 원래 형식 그대로 리턴! (이 부분이 핵심입니다)
    return {
        "city": "기상청 날씨",
        "temp": temp,
        "feels_like": temp,
        "temp_max": temp + 2, 
        "temp_min": temp - 2,
        "humidity": humidity,
        "wind_speed": wind_speed,
        "description": desc,
        "icon": icon,
    }

def get_hourly_forecast(lat: float, lon: float, count: int = 8):
    nx, ny = get_nx_ny(lat, lon)
    base_date, base_time = get_kma_base_time()
    
    url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    params = {
        "serviceKey": KMA_API_KEY,
        "pageNo": "1", "numOfRows": "200", "dataType": "JSON",
        "base_date": base_date, "base_time": base_time, "nx": nx, "ny": ny
    }
    
    try:
        res = requests.get(url, params=params, timeout=5)
        items = res.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
    except Exception:
        items = []

    forecast_dict = {}
    for item in items:
        ftime = f"{item['fcstDate']} {item['fcstTime']}"
        if ftime not in forecast_dict:
            forecast_dict[ftime] = {"temp": 0, "sky": "1", "pty": "0", "pop": 0}
        
        cat = item["category"]
        val = item["fcstValue"]
        if cat == "TMP": forecast_dict[ftime]["temp"] = float(val)
        elif cat == "SKY": forecast_dict[ftime]["sky"] = val
        elif cat == "PTY": forecast_dict[ftime]["pty"] = val
        elif cat == "POP": forecast_dict[ftime]["pop"] = float(val) / 100.0
        
    result = []
    for ftime, f_data in list(forecast_dict.items())[:count]:
        desc = "맑음"; icon = "01d"
        pty = f_data["pty"]; sky = f_data["sky"]
        
        if pty in ["1", "4"]: desc = "비"; icon = "09d"
        elif pty == "3": desc = "눈"; icon = "13d"
        elif pty == "2": desc = "비/눈"; icon = "13d"
        elif sky == "3": desc = "구름많음"; icon = "03d"
        elif sky == "4": desc = "흐림"; icon = "04d"
        
        # 💡 시간별 예보도 원래 프론트엔드 포맷으로 리턴
        result.append({
            "time": f"{ftime[8:10]}시",
            "temp": f_data["temp"],
            "pop": f_data["pop"],
            "description": desc,
            "icon": icon
        })
    return result

# 검색과 대기질은 OWM 유지 (.env 키 사용)
def search_location(q: str):
    res = requests.get(
        "https://api.openweathermap.org/geo/1.0/direct",
        params={"q": q, "limit": 1, "appid": OWM_API_KEY}, timeout=5)
    data = res.json()
    if not data: return None
    return {"name": data[0]["name"], "lat": data[0]["lat"], "lon": data[0]["lon"]}

def get_air_quality(lat: float, lon: float):
    res = requests.get(
        f"{BASE}/air_pollution",
        params={"lat": lat, "lon": lon, "appid": OWM_API_KEY}, timeout=5)
    item = res.json()["list"][0]
    pm25 = item["components"]["pm2_5"]

    if pm25 <= 15: level = "좋음"
    elif pm25 <= 35: level = "보통"
    elif pm25 <= 75: level = "나쁨"
    else: level = "매우나쁨"
    return {"pm25": round(pm25), "pm10": round(item["components"]["pm10"]), "level": level}