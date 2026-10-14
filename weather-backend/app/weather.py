import os
import requests
import math
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# 기상청 API 키 및 OpenWeatherMap API 키
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
    kst = timezone(timedelta(hours=9))
    today_str = datetime.now(kst).strftime("%Y%m%d")
    
    url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    params = {
        "serviceKey": KMA_API_KEY,
        # 오늘 하루치 최고/최저 기온을 모두 찾기 위해 300개를 요청합니다.
        "pageNo": "1", "numOfRows": "300", "dataType": "JSON",
        "base_date": base_date, "base_time": base_time, "nx": nx, "ny": ny
    }
    
    try:
        res = requests.get(url, params=params, timeout=5)
        items = res.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
    except Exception:
        items = []

    temp = 20.0; humidity = 50; wind_speed = 0.0; sky = "1"; pty = "0"
    tmx = None; tmn = None
    today_temps = []
    
    if items:
        # 현재와 가장 가까운 예보 시간의 데이터 추출
        target_date = items[0].get("fcstDate")
        target_time = items[0].get("fcstTime")
        
        for item in items:
            f_date = item.get("fcstDate")
            f_time = item.get("fcstTime")
            cat = item.get("category")
            val = item.get("fcstValue", "0")
            
            # 1) 현재 날씨 상태 추출
            if f_date == target_date and f_time == target_time:
                if cat == "TMP": temp = float(val)
                elif cat == "REH": humidity = float(val)
                elif cat == "WSD": wind_speed = float(val)
                elif cat == "SKY": sky = str(val)
                elif cat == "PTY": pty = str(val)
                
            # 2) 오늘 날짜의 실제 최고기온(TMX), 최저기온(TMN) 및 시간별 기온 수집
            if f_date == today_str:
                if cat == "TMX": tmx = float(val)
                elif cat == "TMN": tmn = float(val)
                elif cat == "TMP": today_temps.append(float(val))

    # 💡 기상청 발표 시간에 따라 TMX/TMN이 지나갔을 경우, 오늘 시간별 온도 중 최고/최저값으로 정확히 보완
    if today_temps:
        temp_max = round(tmx if tmx is not None else max(today_temps))
        temp_min = round(tmn if tmn is not None else min(today_temps))
    else:
        temp_max = round(tmx if tmx is not None else temp + 2)
        temp_min = round(tmn if tmn is not None else temp - 2)

    # 현재 온도가 최고/최저 범위를 벗어나지 않도록 자연스럽게 보정
    if temp > temp_max: temp_max = round(temp)
    if temp < temp_min: temp_min = round(temp)

    # 💡 OpenWeatherMap에서 실제 일출(sunrise), 일몰(sunset), 체감온도(feels_like) 가져오기
    sunrise_str = "06:00"
    sunset_str = "18:30"
    feels_like = temp
    try:
        owm_res = requests.get(
            f"{BASE}/weather",
            params={"lat": lat, "lon": lon, "appid": OWM_API_KEY, "units": "metric"},
            timeout=5
        )
        if owm_res.status_code == 200:
            owm_data = owm_res.json()
            sys_data = owm_data.get("sys", {})
            if "sunrise" in sys_data:
                sunrise_str = datetime.fromtimestamp(sys_data["sunrise"], kst).strftime("%H:%M")
            if "sunset" in sys_data:
                sunset_str = datetime.fromtimestamp(sys_data["sunset"], kst).strftime("%H:%M")
            if "main" in owm_data and "feels_like" in owm_data["main"]:
                feels_like = round(owm_data["main"]["feels_like"], 1)
    except Exception:
        pass

    desc = "맑음"; icon = "01d"
    if pty in ["1", "4"]: desc = "비"; icon = "09d"
    elif pty == "3": desc = "눈"; icon = "13d"
    elif pty == "2": desc = "비/눈"; icon = "13d"
    elif sky == "3": desc = "구름많음"; icon = "03d"
    elif sky == "4": desc = "흐림"; icon = "04d"

    return {
        "city": "기상청 실시간",
        "temp": round(temp),
        "feels_like": round(feels_like),
        "temp_max": temp_max, 
        "temp_min": temp_min,
        "humidity": int(humidity),
        "wind_speed": wind_speed,
        "description": desc,
        "icon": icon,
        "sunrise": sunrise_str, # 💡 실제 일출 시간 추가
        "sunset": sunset_str,   # 💡 실제 일몰 시간 추가
    }

def get_hourly_forecast(lat: float, lon: float, count: int = 24):
    nx, ny = get_nx_ny(lat, lon)
    base_date, base_time = get_kma_base_time()
    
    url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    params = {
        "serviceKey": KMA_API_KEY,
        "pageNo": "1", "numOfRows": "350", "dataType": "JSON",
        "base_date": base_date, "base_time": base_time, "nx": nx, "ny": ny
    }
    
    try:
        res = requests.get(url, params=params, timeout=5)
        items = res.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
    except Exception:
        items = []

    kst = timezone(timedelta(hours=9))
    now_str = datetime.now(kst).strftime("%Y%m%d %H00")

    forecast_dict = {}
    for item in items:
        ftime = f"{item['fcstDate']} {item['fcstTime']}"
        if ftime < now_str:
            continue
            
        if ftime not in forecast_dict:
            forecast_dict[ftime] = {"temp": 0.0, "sky": "1", "pty": "0", "pop": 0.0}
        
        cat = item["category"]
        val = item["fcstValue"]
        if cat == "TMP": forecast_dict[ftime]["temp"] = float(val)
        elif cat == "SKY": forecast_dict[ftime]["sky"] = str(val)
        elif cat == "PTY": forecast_dict[ftime]["pty"] = str(val)
        elif cat == "POP": forecast_dict[ftime]["pop"] = float(val) / 100.0
        
    result = []
    sorted_times = sorted(forecast_dict.keys())[:count]
    
    for ftime in sorted_times:
        f_data = forecast_dict[ftime]
        desc = "맑음"; icon = "01d"
        pty = f_data["pty"]; sky = f_data["sky"]
        
        if pty in ["1", "4"]: desc = "비"; icon = "09d"
        elif pty == "3": desc = "눈"; icon = "13d"
        elif pty == "2": desc = "비/눈"; icon = "13d"
        elif sky == "3": desc = "구름많음"; icon = "03d"
        elif sky == "4": desc = "흐림"; icon = "04d"
        
        hour_num = int(ftime[9:11])
        
        result.append({
            "time": f"{hour_num}시",
            "temp": round(f_data["temp"]),
            "pop": f_data["pop"],
            "description": desc,
            "icon": icon
        })
    return result

def search_location(q: str):
    try:
        res = requests.get(
            "https://api.openweathermap.org/geo/1.0/direct",
            params={"q": q, "limit": 1, "appid": OWM_API_KEY}, timeout=5)
        
        if res.status_code != 200:
            return None
            
        data = res.json()
        if not isinstance(data, list) or len(data) == 0:
            return None
            
        # 한국어 도시 이름이 있으면 우선 사용
        local_names = data[0].get("local_names", {})
        kor_name = local_names.get("ko", data[0].get("name", q))
        
        return {"name": kor_name, "lat": data[0].get("lat", 0), "lon": data[0].get("lon", 0)}
    except Exception:
        return None

def get_air_quality(lat: float, lon: float):
    try:
        res = requests.get(
            f"{BASE}/air_pollution",
            params={"lat": lat, "lon": lon, "appid": OWM_API_KEY}, timeout=5)
            
        if res.status_code != 200:
            return {"pm25": 0, "pm10": 0, "level": "점검중"}
            
        data = res.json()
        if "list" not in data or len(data["list"]) == 0:
            return {"pm25": 0, "pm10": 0, "level": "점검중"}
            
        item = data["list"][0]
        pm25 = item.get("components", {}).get("pm2_5", 0)
        pm10 = item.get("components", {}).get("pm10", 0)

        if pm25 <= 15: level = "좋음"
        elif pm25 <= 35: level = "보통"
        elif pm25 <= 75: level = "나쁨"
        else: level = "매우나쁨"
        return {"pm25": round(pm25), "pm10": round(pm10), "level": level}
    except Exception:
        return {"pm25": 0, "pm10": 0, "level": "점검중"}