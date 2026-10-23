import os
import requests
import math
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

KMA_API_KEY = os.getenv("KMA_API_KEY")
OWM_API_KEY = os.getenv("OPENWEATHER_API_KEY")
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

# 💡 어제 같은 시각의 기온을 가져오는 함수 (기상청 단기예보 + Open-Meteo 백업)
def get_yesterday_temp(lat: float, lon: float, nx: int, ny: int) -> float | None:
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst)
    yesterday = now - timedelta(days=1)
    y_date_str = yesterday.strftime("%Y%m%d")
    target_hour_str = now.strftime("%H00")

    # 1차 시도: 기상청 어제 예보 데이터 조회
    try:
        url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
        params = {
            "serviceKey": KMA_API_KEY,
            "pageNo": "1", "numOfRows": "300", "dataType": "JSON",
            "base_date": y_date_str, "base_time": "0200", "nx": nx, "ny": ny
        }
        res = requests.get(url, params=params, timeout=3)
        items = res.json().get("response", {}).get("body", {}).get("items", {}).get("item", [])
        for item in items:
            if item.get("fcstDate") == y_date_str and item.get("fcstTime") == target_hour_str and item.get("category") == "TMP":
                return float(item.get("fcstValue"))
    except Exception:
        pass

    # 2차 시도 (새벽 0~2시 등 기상청 어제 02시 기준을 벗어날 때): 무료 Open-Meteo 과거 날씨 조회
    try:
        om_url = "https://api.open-meteo.com/v1/forecast"
        om_params = {
            "latitude": lat, "longitude": lon,
            "hourly": "temperature_2m",
            "past_days": 1, "forecast_days": 1,
            "timezone": "Asia/Seoul"
        }
        om_res = requests.get(om_url, params=om_params, timeout=3)
        if om_res.status_code == 200:
            hourly = om_res.json().get("hourly", {})
            times = hourly.get("time", [])
            temps = hourly.get("temperature_2m", [])
            target_iso = yesterday.strftime("%Y-%m-%dT%H:00")
            if target_iso in times:
                idx = times.index(target_iso)
                if idx < len(temps) and temps[idx] is not None:
                    return float(temps[idx])
    except Exception:
        pass

    return None

def get_current_weather(lat: float, lon: float):
    nx, ny = get_nx_ny(lat, lon)
    base_date, base_time = get_kma_base_time()
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst)
    today_str = now.strftime("%Y%m%d")
    
    url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    params = {
        "serviceKey": KMA_API_KEY,
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
        target_date = items[0].get("fcstDate")
        target_time = items[0].get("fcstTime")
        for item in items:
            f_date = item.get("fcstDate")
            f_time = item.get("fcstTime")
            cat = item.get("category")
            val = item.get("fcstValue", "0")
            
            if f_date == target_date and f_time == target_time:
                if cat == "TMP": temp = float(val)
                elif cat == "REH": humidity = float(val)
                elif cat == "WSD": wind_speed = float(val)
                elif cat == "SKY": sky = str(val)
                elif cat == "PTY": pty = str(val)
                
            if f_date == today_str:
                if cat == "TMX": tmx = float(val)
                elif cat == "TMN": tmn = float(val)
                elif cat == "TMP": today_temps.append(float(val))

    if today_temps:
        temp_max = round(tmx if tmx is not None else max(today_temps))
        temp_min = round(tmn if tmn is not None else min(today_temps))
    else:
        temp_max = round(tmx if tmx is not None else temp + 2)
        temp_min = round(tmn if tmn is not None else temp - 2)

    if temp > temp_max: temp_max = round(temp)
    if temp < temp_min: temp_min = round(temp)

    sunrise_str = "06:00"; sunset_str = "18:30"
    feels_like = temp
    is_day = True 

    try:
        owm_res = requests.get(
            f"{BASE}/weather",
            params={"lat": lat, "lon": lon, "appid": OWM_API_KEY, "units": "metric"},
            timeout=5
        )
        if owm_res.status_code == 200:
            owm_data = owm_res.json()
            sys_data = owm_data.get("sys", {})
            now_ts = now.timestamp()
            
            if "sunrise" in sys_data and "sunset" in sys_data:
                sunrise_str = datetime.fromtimestamp(sys_data["sunrise"], kst).strftime("%H:%M")
                sunset_str = datetime.fromtimestamp(sys_data["sunset"], kst).strftime("%H:%M")
                if now_ts < sys_data["sunrise"] or now_ts > sys_data["sunset"]:
                    is_day = False
                    
            if "main" in owm_data and "feels_like" in owm_data["main"]:
                feels_like = round(owm_data["main"]["feels_like"], 1)
    except Exception:
        is_day = 6 <= now.hour < 18

    # 💡 어제 기온과의 차이 계산
    yesterday_temp = get_yesterday_temp(lat, lon, nx, ny)
    temp_diff = round(temp - yesterday_temp) if yesterday_temp is not None else 0

    suffix = "d" if is_day else "n"
    desc = "맑음"; icon = f"01{suffix}"
    if pty in ["1", "4"]: desc = "비"; icon = f"09{suffix}"
    elif pty == "3": desc = "눈"; icon = f"13{suffix}"
    elif pty == "2": desc = "비/눈"; icon = f"13{suffix}"
    elif sky == "3": desc = "구름많음"; icon = f"03{suffix}"
    elif sky == "4": desc = "흐림"; icon = f"04{suffix}"

    return {
        "city": "기상청 실시간",
        "temp": round(temp),
        "feels_like": round(feels_like),
        "temp_max": temp_max,
        "temp_min": temp_min,
        "temp_diff": temp_diff,
        "humidity": int(humidity),
        "wind_speed": wind_speed,
        "description": desc,
        "icon": icon,
        "sunrise": sunrise_str,
        "sunset": sunset_str,
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
        if ftime < now_str: continue
            
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
        pty = f_data["pty"]; sky = f_data["sky"]
        hour_num = int(ftime[9:11])
        
        is_day = 6 <= hour_num < 18
        suffix = "d" if is_day else "n"
        
        desc = "맑음"; icon = f"01{suffix}"
        if pty in ["1", "4"]: desc = "비"; icon = f"09{suffix}"
        elif pty == "3": desc = "눈"; icon = f"13{suffix}"
        elif pty == "2": desc = "비/눈"; icon = f"13{suffix}"
        elif sky == "3": desc = "구름많음"; icon = f"03{suffix}"
        elif sky == "4": desc = "흐림"; icon = f"04{suffix}"
        
        result.append({
            "time": f"{hour_num}시", "temp": round(f_data["temp"]),
            "pop": f_data["pop"], "description": desc, "icon": icon
        })
    return result

def get_weekly_forecast(lat: float, lon: float):
    try:
        res = requests.get(
            f"{BASE}/forecast",
            params={"lat": lat, "lon": lon, "appid": OWM_API_KEY, "units": "metric", "lang": "kr"},
            timeout=5
        )
        if res.status_code != 200: return []
        data = res.json()
        
        daily_data = {}
        for item in data.get("list", []):
            dt_txt = item.get("dt_txt", "")
            if not dt_txt: continue
            
            date_str = dt_txt.split(" ")[0]
            main_data = item.get("main", {})
            temp = main_data.get("temp", 20)
            pop = item.get("pop", 0)
            weather_list = item.get("weather", [])
            icon = weather_list[0].get("icon", "01d") if weather_list else "01d"
            
            if date_str not in daily_data:
                daily_data[date_str] = {"temps": [], "pops": [], "icons": []}
            daily_data[date_str]["temps"].append(temp)
            daily_data[date_str]["pops"].append(pop)
            daily_data[date_str]["icons"].append(icon)
        
        result = []
        kst = timezone(timedelta(hours=9))
        today_str = datetime.now(kst).strftime("%Y-%m-%d")
        days_kr = ["월", "화", "수", "목", "금", "토", "일"]
        
        for d_str, d_info in daily_data.items():
            try:
                d_obj = datetime.strptime(d_str, "%Y-%m-%d")
                weekday = days_kr[d_obj.weekday()]
            except Exception:
                continue
            
            icons = d_info["icons"]
            day_icons = [ic for ic in icons if "d" in ic]
            rep_icon = day_icons[len(day_icons)//2] if day_icons else (icons[0] if icons else "01d")
            
            temps = d_info["temps"]
            pops = d_info["pops"]
            
            if not temps: temps = [20]
            if not pops: pops = [0]
            
            result.append({
                "date": f"{d_obj.month}/{d_obj.day}",
                "day": weekday,
                "isToday": d_str == today_str,
                "temp_max": round(max(temps)),
                "temp_min": round(min(temps)),
                "pop": round(max(pops) * 100),
                "icon": rep_icon
            })
        return result[:5]
    except Exception:
        return []

def search_location(q: str):
    try:
        res = requests.get(
            "https://api.openweathermap.org/geo/1.0/direct",
            params={"q": q, "limit": 1, "appid": OWM_API_KEY}, timeout=5)
        if res.status_code != 200: return None
        data = res.json()
        if not isinstance(data, list) or len(data) == 0: return None
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
        if res.status_code != 200: return {"pm25": 0, "pm10": 0, "level": "점검중"}
        data = res.json()
        if "list" not in data or len(data["list"]) == 0: return {"pm25": 0, "pm10": 0, "level": "점검중"}
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