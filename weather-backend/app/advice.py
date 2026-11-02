import os
import time
import random
from datetime import datetime, timedelta, timezone
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

AI_CACHE = {}
CACHE_TTL = 1800  # 30분 캐싱

def get_kst_hour() -> int:
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst).hour

# 💡 1. 일반 도시용 한 줄 멘트 생성 함수
def make_advice(current: dict, forecast: list) -> list:
    hour = get_kst_hour()
    temp = round(current.get("temp", 20))
    feels_like = round(current.get("feels_like", temp))
    wind = current.get("wind_speed", 0)
    desc = current.get("description", "맑음")
    icon = current.get("icon", "")
    max_pop = max((f.get("pop", 0) for f in forecast[:6]), default=0) if forecast else 0

    is_night = ("n" in icon) or (hour >= 21 or hour < 6)

    if "비" in desc or "소나기" in desc:
        if is_night:
            return [random.choice([
                "🌧️ 빗소리가 들리는 밤이네요. 창문을 잘 닫고 포근한 밤 보내세요!",
                "☔ 밤사이 비가 내리고 있어요. 내일 아침 출근·등교길 우산 꼭 챙기세요!"
            ])]
        return [random.choice([
            "☔ 지금 비가 내리고 있어요. 외출하실 때 우산 잊지 마세요!",
            "🌧️ 비 내리는 하루네요. 빗길 미끄럼 주의하시고 안전한 하루 보내세요!"
        ])]
        
    if "눈" in desc:
        return ["❄️ 눈이 내리고 있어요! 빙판길 조심하시고 따뜻하게 입으세요."]

    if max_pop >= 0.5:
        if is_night:
            return ["🌂 밤이나 내일 오전 중 비 소식이 있어요. 머리맡에 우산을 미리 챙겨두세요!"]
        return ["☔ 곧 비가 올 확률이 높아요. 가방 속에 작은 우산 하나 꼭 챙기세요!"]

    if wind >= 8:
        if is_night:
            return ["💨 밤바람이 꽤 강하게 불고 있어요. 창문 단속 잘하시고 따뜻한 밤 보내세요!"]
        return ["💨 바람이 강하게 불어 체감상 더 쌀쌀해요. 바람막이나 겉옷을 꼭 챙기세요!"]

    if 0 <= hour < 6:
        if feels_like <= 10:
            return [f"🌙 새벽 공기가 {temp}°C로 많이 차가워요. 이불 꼭 덮고 따뜻하게 주무세요!"]
        return ["🌙 고요하고 선선한 새벽이에요. 편안하고 좋은 꿈 꾸시길 바랄게요!"]
    elif 6 <= hour < 11:
        if feels_like <= 8:
            return [f"🧥 아침 기온이 {temp}°C로 꽤 추워요! 든든한 외투 챙겨 입고 활기차게 출발하세요."]
        return ["🌤️ 상쾌한 아침이에요! 일교차에 대비해 가벼운 겉옷 하나 챙기세요."]
    elif 11 <= hour < 17:
        if feels_like >= 28:
            return ["☀️ 조금 덥게 느껴질 수 있는 낮이에요. 시원한 물 자주 마시며 보내세요!"]
        elif feels_like <= 8:
            return ["🥶 낮에도 바람이 차갑네요. 따뜻한 음료와 함께 포근한 오후 보내세요!"]
        return ["☀️ 활동하기 쾌적한 오후예요! 가볍게 산책하며 기분 전환해 보세요."]
    elif 17 <= hour < 21:
        if feels_like <= 14:
            return ["🌇 해가 지면서 공기가 금세 차가워졌어요. 귀갓길 따뜻하게 입으세요!"]
        return ["🌇 선선한 바람이 불어 저녁 산책하기 딱 좋은 날씨예요. 오늘 하루도 고생 많으셨어요!"]
    else:
        if feels_like <= 12:
            return [f"🌙 밤기온이 {temp}°C까지 내려가 제법 쌀쌀해요. 창문 잘 닫고 포근한 밤 보내세요!"]
        return ["🌙 선선하고 쾌적한 밤이에요. 오늘 하루도 고생 많으셨습니다, 편안한 밤 보내세요!"]

# 💡 2. 일반 도시용 비상 상세 브리핑 생성 함수
def make_fallback_briefing(current: dict, forecast: list, air_quality: dict) -> str:
    hour = get_kst_hour()
    temp = round(current.get("temp", 20))
    feels_like = round(current.get("feels_like", temp))
    desc = current.get("description", "맑음")
    wind = current.get("wind_speed", 0)
    pm = air_quality.get("level", "보통") if air_quality else "보통"
    max_pop = int(max((f.get("pop", 0) for f in forecast[:6]), default=0) * 100) if forecast else 0

    greeting = (
        "고요한 새벽 시간입니다. 🌙" if 0 <= hour < 6 else
        "상쾌한 아침입니다! ☀️" if 6 <= hour < 11 else
        "활기찬 오후 시간입니다! 🌤️" if 11 <= hour < 17 else
        "하루를 마무리하는 저녁 시간입니다. 🌇" if 17 <= hour < 21 else
        "편안한 밤 시간입니다. ✨"
    )
    rain_info = f"향후 강수 확률이 {max_pop}%로 예보되어 있으니 외출 시 작은 우산을 챙기시는 것이 좋겠습니다. " if max_pop >= 40 else ""

    return (
        f"{greeting} 현재 하늘은 {desc} 상태이며, 기온은 {temp}°C(체감 {feels_like}°C), 풍속은 {wind}m/s를 기록하고 있습니다. "
        f"미세먼지 농도는 '{pm}' 수준을 보이고 있습니다. {rain_info}일교차와 기온 변화에 유의하시어 건강하고 쾌적한 시간 보내시길 바랍니다."
    )

# 💡 3. 산악 모드 전용 한 줄 멘트 & 상세 등산 브리핑 생성 함수 (Fallback 포함)
def make_mountain_fallback(current: dict, forecast: list, mountain_info: dict) -> dict:
    name = mountain_info.get("name", "산")
    peak = mountain_info.get("peak", "정상")
    elevation = mountain_info.get("elevation", 800)
    terrain = mountain_info.get("terrain", "암릉")
    course_tip = mountain_info.get("course_tip", "")
    safe_descent = mountain_info.get("safe_descent_time", "15:30")
    summit_drop = mountain_info.get("summit_drop", 5)

    temp = round(current.get("temp", 10))
    feels_like = round(current.get("feels_like", temp))
    wind = current.get("wind_speed", 3)
    humidity = current.get("humidity", 60)
    desc = current.get("description", "맑음")
    max_pop = int(max((f.get("pop", 0) for f in forecast[:6]), default=0) * 100) if forecast else 0

    if "비" in desc or "눈" in desc or max_pop >= 60:
        one_line = f"⚠️ {name}({peak})에 강수·미끄럼 위험이 높아요! 무리한 산행을 자제하고 방수 장비와 아이젠을 챙기세요."
    elif temp <= 2:
        one_line = f"🧊 {name} 정상 체감 {feels_like}°C! 등산로 결빙(블랙아이스) 주의하시고 아이젠·방풍재킷을 꼭 챙기세요."
    elif wind >= 7:
        one_line = f"💨 {name} 능선에 {wind}m/s의 강한 바람이 불어요! 정상 체류 시 저체온증 예방을 위해 방풍 의류가 필수입니다."
    elif humidity >= 80 and wind <= 3:
        one_line = f"🌊 {name} 정상 습도가 높아 이른 아침 멋진 운해(구름바다)가 기대돼요! 안전 하산 시각({safe_descent})을 꼭 지켜주세요."
    else:
        one_line = f"🧗 {name}({peak}, {elevation}m) 산행하기 좋은 기상이에요! 평지보다 {summit_drop}°C 낮으니 겹쳐 입을 겉옷을 배낭에 챙기세요."

    view_comment = (
        "습도가 높고 바람이 약해 일출 전후로 장엄한 운해(구름바다)를 만날 가능성이 높으나 안개(곰탕)로 시야가 좁아질 수 있습니다."
        if humidity >= 82 else
        "기온이 영하권이고 습도가 높아 나뭇가지에 피어난 아름다운 상고대(눈꽃) 절경을 기대할 수 있습니다."
        if temp <= -2 and humidity >= 75 else
        "대기가 비교적 맑고 시정이 좋아 정상에서 탁 트인 파노라마 조망(뻥뷰)을 감상하기 좋습니다."
    )

    briefing = (
        f"현재 {name} {peak}(해발 {elevation}m) 기준 기온은 {temp}°C(체감 {feels_like}°C)로 산 입구 평지보다 약 {summit_drop}°C 더 낮으며, "
        f"능선 풍속은 {wind}m/s를 기록하고 있습니다. {view_comment} "
        f"{name}은(는) '{terrain}' 지형 특성상 기온 하강 시 바위나 계단이 미끄러울 수 있으니 {course_tip} "
        f"산속은 일몰 전 급격히 어두워지므로 늦어도 {safe_descent} 이전에는 하산을 시작하시기 바랍니다."
    )
    return {"one_line": one_line, "briefing": briefing}

# 💡 4. 메인 AI 호출 및 캐싱 통합 함수 (일반 모드 / 산악 모드 자동 분기)
def get_ai_contents(current: dict, forecast: list, air_quality: dict, mountain_info: dict | None = None) -> dict:
    if mountain_info:
        fb = make_mountain_fallback(current, forecast, mountain_info)
        fallback_one_line = fb["one_line"]
        fallback_briefing = fb["briefing"]
    else:
        fallback_one_line = " ".join(make_advice(current, forecast))
        fallback_briefing = make_fallback_briefing(current, forecast, air_quality)
    
    if not GEMINI_API_KEY:
        return {"one_line": fallback_one_line, "briefing": fallback_briefing}

    hour = get_kst_hour()
    city = mountain_info.get("name") if mountain_info else current.get("city", "default")
    temp = round(current.get("temp", 20))
    desc = current.get("description", "맑음")
    mode_tag = "MTN" if mountain_info else "CITY"
    cache_key = f"{mode_tag}_{city}_{hour}_{desc}_{temp}"

    now_ts = time.time()
    if cache_key in AI_CACHE:
        saved_time, saved_data = AI_CACHE[cache_key]
        if now_ts - saved_time < CACHE_TTL:
            return saved_data

    feels_like = round(current.get("feels_like", temp))
    wind = current.get("wind_speed", 0)
    humidity = current.get("humidity", 50)
    pm = air_quality.get("level", "보통") if air_quality else "보통"
    max_pop = int(max((f.get("pop", 0) for f in forecast[:6]), default=0) * 100) if forecast else 0

    if mountain_info:
        m_name = mountain_info.get("name", "산")
        m_peak = mountain_info.get("peak", "정상")
        m_elev = mountain_info.get("elevation", 800)
        m_terrain = mountain_info.get("terrain", "암릉")
        m_tip = mountain_info.get("course_tip", "")
        m_descent = mountain_info.get("safe_descent_time", "15:30")
        m_drop = mountain_info.get("summit_drop", 5)

        prompt = (
            f"당신은 대한민국 국립공원 산악 안전 및 등산 기상 전문 가이드입니다.\n"
            f"대상 산: {m_name} {m_peak} (해발 {m_elev}m, 지형: {m_terrain})\n"
            f"산 정상 기상: 현재 시각 {hour}시, 날씨 {desc}, 정상 기온 {temp}도(체감 {feels_like}도, 평지 입구보다 {m_drop}도 낮음), "
            f"풍속 {wind}m/s, 습도 {humidity}%, 미세먼지 {pm}, 강수확률 {max_pop}%, 권장 하산 마감 시각 {m_descent}\n"
            f"산행 참고 팁: {m_tip}\n\n"
            "마크다운(**, ## 등)은 절대 사용하지 말고 평문과 이모지만 사용해 아래 두 항목을 작성해주세요.\n"
            "1. 한줄멘트: 등산객에게 가장 중요한 핵심 포인트(정상 체감추위/결빙/바위미끄럼/운해·조망/하산시간 중 1~2개)를 짚어주는 강렬하고 실용적인 한 문장\n"
            "2. 상세브리핑: ①들머리 대비 정상 기온·바람 체감 분석 ②오늘 조망 확률(운해/상고대/쾌청 뻥뷰/안개 곰탕 여부) ③지형 특성을 고려한 노면 위험도 및 레이어링·장비(스틱, 아이젠, 방풍재킷 등) 추천과 안전 하산 시각 안내를 포함해 3~4문장으로 전문성 있게 작성\n\n"
            "반드시 아래 출력 형식을 정확히 지켜서 답변해주세요:\n"
            "한줄: [여기에 한줄멘트 작성]\n"
            "상세: [여기에 상세브리핑 작성]"
        )
    else:
        time_context = (
            "새벽(취침/휴식 시간)" if 0 <= hour < 6 else
            "아침(출근/등교 시간)" if 6 <= hour < 11 else
            "낮(오후 활동 시간)" if 11 <= hour < 17 else
            "저녁(퇴근/여가 시간)" if 17 <= hour < 21 else
            "밤(하루 마무리/취침 전 시간)"
        )
        prompt = (
            f"현재 시각은 {hour}시({time_context})이며, 날씨는 {desc}, 기온은 {temp}도(체감 {feels_like}도), 풍속은 {wind}m/s, 미세먼지는 {pm}, 강수확률은 {max_pop}%입니다.\n"
            "아래 두 가지 항목을 작성해주세요. 마크다운(**, ## 등)은 절대 사용하지 말고 평문과 이모지만 사용하세요.\n\n"
            f"1. 한줄멘트: 현재 시간대({time_context})와 날씨 상황에 딱 맞는 센스 있고 다정한 멘트 딱 한 문장 (밤이나 새벽이면 편안한 밤/숙면 인사를 건넬 것)\n"
            f"2. 상세브리핑: 기상캐스터처럼 현재 시간대({time_context})에 어울리는 다정하고 전문적인 날씨 브리핑 3~4문장\n\n"
            "반드시 아래 출력 형식을 정확히 지켜서 답변해주세요:\n"
            "한줄: [여기에 한줄멘트 작성]\n"
            "상세: [여기에 상세브리핑 작성]"
        )
    
    try:
        target_model = "gemini-1.5-flash"
        model = genai.GenerativeModel(target_model)
        response = model.generate_content(prompt)
        
        if response and response.text:
            text = response.text.strip()
            one_line = fallback_one_line
            briefing = text
            
            if "한줄:" in text and "상세:" in text:
                parts = text.split("상세:")
                one_line = parts[0].replace("한줄:", "").strip()
                briefing = parts[1].strip()
            else:
                lines = [line.strip() for line in text.split("\n") if line.strip()]
                if len(lines) >= 2:
                    one_line = lines[0].replace("한줄:", "").strip()
                    briefing = " ".join(lines[1:]).replace("상세:", "").strip()
            
            result = {"one_line": one_line, "briefing": briefing}
            AI_CACHE[cache_key] = (now_ts, result)
            return result
        else:
            return {"one_line": fallback_one_line, "briefing": fallback_briefing}
    except Exception:
        return {"one_line": fallback_one_line, "briefing": fallback_briefing}