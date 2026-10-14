import os
import requests
import math
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# 발급받으신 기상청 API 인증키
KMA_API_KEY = "b82bdd286e9c433b217611deeca7d0a07c8479a8eef0bf54322e76a9db669c12"

# (유지) 지역 검색과 미세먼지를 위한 기존 OWM 키
OWM_API_KEY = os.getenv("OPENWEATHER_API_KEY")

# [1] 기상청 전용: 위경도를 X, Y 격자 좌표로 변환하는 공식
def get_nx_ny(lat, lon):
    RE = 6371.00877; GRID = 5.0; SLAT1 = 30.0; SLAT2 = 60.0; OLON = 126.0; OLAT = 38.0; XO = 43; YO = 136
    DEGRAD = math.pi / 180.0
    re = RE / GRID
    slat1 = SLAT1 * DEGRAD
    slat2 = SLAT2 * DEGRAD
    olon = OLON * DEGRAD
    olat = OLAT * DEGRAD
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

# [2] 기상청 전용: 단기예보 발표 기준 시간 계산 (02, 05, 08, 11, 14, 17, 20, 23시)
def get_kma_base_time():
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst) - timedelta(hours=1) # 안정적인 조회를 위해 1시간 전 기준
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

# [3] 기상청 API 연동: 현재 날씨
def get_current_weather(lat: float, lon: float):
    nx, ny = get_nx_ny(lat, lon)
    base_date, base_time = get_kma_base_time()
    
    url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    params = {
        "serviceKey": KMA_API_KEY,
        "pageNo": "1",
        "numOfRows": "50",
        "dataType": "JSON",
        "base_date": base_date,
        "base_time": base_time,
        "nx": nx,
        "ny": ny
    }
    
    res = requests.get(url, params=params)
    items = res.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
    
    temp = 0; humidity = 0; sky = "1"; pty = "0"
    for item in items:
        if item.get("category") == "TMP": temp = float(item.get("fcstValue"))
        elif item.get("category") == "REH": humidity = float(item.get("fcstValue"))
        elif item.get("category") == "SKY": sky = item.get("fcstValue")
        elif item.get("category") == "PTY": pty = item.get("fcstValue")
        
    desc = "맑음"
    if pty in ["1", "4"]: desc = "비"
    elif pty == "3": desc = "눈"
    elif pty == "2": desc = "비/눈"
    elif sky == "3": desc = "구름많음"
    elif sky == "4": desc = "흐림"
        
    return {
        "weather": [{"description": desc, "icon": "01d"}],
        "main": {"temp": temp, "humidity": humidity},
        "name": "기상청 실시간 관측"
    }

# [4] 기상청 API 연동: 시간별 날씨
def get_hourly_forecast(lat: float, lon: float):
    nx, ny = get_nx_ny(lat, lon)
    base_date, base_time = get_kma_base_time()
    
    url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    params = {
        "serviceKey": KMA_API_KEY,
        "pageNo": "1",
        "numOfRows": "200",
        "dataType": "JSON",
        "base_date": base_date,
        "base_time": base_time,
        "nx": nx,
        "ny": ny
    }
    
    res = requests.get(url, params=params)
    items = res.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
    
    forecast_dict = {}
    for item in items:
        ftime = f"{item['fcstDate']} {item['fcstTime']}"
        if ftime not in forecast_dict:
            forecast_dict[ftime] = {"temp": 0, "sky": "1", "pty": "0"}
        
        if item["category"] == "TMP": forecast_dict[ftime]["temp"] = float(item["fcstValue"])
        elif item["category"] == "SKY": forecast_dict[ftime]["sky"] = item["fcstValue"]
        elif item["category"] == "PTY": forecast_dict[ftime]["pty"] = item["fcstValue"]
        
    hourly_list = []
    for ftime, f_data in list(forecast_dict.items())[:8]:
        desc = "맑음"
        if f_data["pty"] in ["1", "4"]: desc = "비"
        elif f_data["pty"] == "3": desc = "눈"
        elif f_data["pty"] == "2": desc = "비/눈"
        elif f_data["sky"] == "3": desc = "구름많음"
        elif f_data["sky"] == "4": desc = "흐림"
        
        formatted_time = f"{ftime[:4]}-{ftime[4:6]}-{ftime[6:8]} {ftime[9:11]}:00:00"
        hourly_list.append({
            "dt_txt": formatted_time,
            "main": {"temp": f_data["temp"]},
            "weather": [{"description": desc, "icon": "01d"}]
        })
        
    return {"list": hourly_list}

# [5] 위치 검색 및 미세먼지는 기상청에 없으므로 기존(OWM) 유지
def search_location(query: str):
    url = f"http://api.openweathermap.org/geo/1.0/direct?q={query}&limit=5&appid={OWM_API_KEY}"
    res = requests.get(url)
    return res.json() if res.status_code == 200 else []

def get_air_quality(lat: float, lon: float):
    url = f"http://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={OWM_API_KEY}"
    res = requests.get(url)
    if res.status_code == 200:
        data = res.json()
        aqi = data['list'][0]['main']['aqi']
        return {"aqi": aqi}
    return {"aqi": 1}