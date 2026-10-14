import os
import requests
from dotenv import load_dotenv

load_dotenv()  # .env 파일을 읽어서 환경변수로 등록
API_KEY = os.getenv("OPENWEATHER_API_KEY")
BASE = "https://api.openweathermap.org/data/2.5"


def get_current_weather(lat: float, lon: float):
    """위도/경도로 현재 날씨를 가져온다."""
    res = requests.get(
        f"{BASE}/weather",
        params={
            "lat": lat,
            "lon": lon,
            "appid": API_KEY,
            "units": "metric",   # 섭씨로 받기
            "lang": "kr",        # 설명을 한국어로 받기
        },
        timeout=5,               # 5초 넘게 응답 없으면 포기
    )
    res.raise_for_status()       # 에러 응답이면 예외 발생
    d = res.json()

    # 프론트가 쓰기 편하게 필요한 값만 골라서 정리
    return {
        "city": d["name"],
        "temp": d["main"]["temp"],
        "feels_like": d["main"]["feels_like"],
        "temp_max": d["main"]["temp_max"],
        "temp_min": d["main"]["temp_min"],
        "humidity": d["main"]["humidity"],
        "wind_speed": d["wind"]["speed"],
        "description": d["weather"][0]["description"],
        "icon": d["weather"][0]["icon"],
    }

from datetime import datetime


def get_hourly_forecast(lat: float, lon: float, count: int = 8):
    """앞으로 count개(3시간 간격)의 예보를 가져온다. 8개 = 24시간."""
    res = requests.get(
        f"{BASE}/forecast",
        params={
            "lat": lat, "lon": lon,
            "appid": API_KEY,
            "units": "metric",
            "lang": "kr",
            "cnt": count,        # 몇 개만 받을지
        },
        timeout=5,
    )
    res.raise_for_status()
    d = res.json()

    result = []
    for item in d["list"]:
        result.append({
            "time": datetime.fromtimestamp(item["dt"]).strftime("%H시"),
            "temp": item["main"]["temp"],
            "pop": item["pop"],              # 강수확률 0~1
            "description": item["weather"][0]["description"],
            "icon": item["weather"][0]["icon"],
        })
    return result
def search_location(q: str):
    res = requests.get(
        "https://api.openweathermap.org/geo/1.0/direct",
        params={"q": q, "limit": 1, "appid": API_KEY}, timeout=5)
    res.raise_for_status()
    data = res.json()
    if not data:
        return None
    return {"name": data[0]["name"], "lat": data[0]["lat"], "lon": data[0]["lon"]}

def get_air_quality(lat: float, lon: float):
    res = requests.get(
        f"{BASE}/air_pollution",
        params={"lat": lat, "lon": lon, "appid": API_KEY},
        timeout=5,
    )
    res.raise_for_status()
    item = res.json()["list"][0]
    pm25 = item["components"]["pm2_5"]

    # 한국 기준 PM2.5 등급
    if pm25 <= 15:
        level = "좋음"
    elif pm25 <= 35:
        level = "보통"
    elif pm25 <= 75:
        level = "나쁨"
    else:
        level = "매우나쁨"
    return {"pm25": round(pm25), "pm10": round(item["components"]["pm10"]), "level": level}
