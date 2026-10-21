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

# 💡 AI 무료 할당량 절약을 위한 캐시 저장소 (30분간 유지)
AI_CACHE = {}
CACHE_TTL = 1800  # 1800초 (30분)

def get_kst_hour() -> int:
    """Render 서버(UTC) 환경에서도 정확한 한국 시간(KST)의 '시(hour)'를 반환합니다."""
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst).hour

# 💡 1. 상황별·시간대별 맞춤형 한 줄 멘트 생성 함수 (AI 한도 초과 시 작동)
def make_advice(current: dict, forecast: list) -> list:
    hour = get_kst_hour()
    temp = round(current.get("temp", 20))
    feels_like = round(current.get("feels_like", temp))
    wind = current.get("wind_speed", 0)
    desc = current.get("description", "맑음")
    icon = current.get("icon", "")
    max_pop = max((f.get("pop", 0) for f in forecast[:6]), default=0) if forecast else 0

    is_night = ("n" in icon) or (hour >= 21 or hour < 6)

    # 1순위: 눈/비 예보 또는 현재 강수 중일 때
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

    # 2순위: 강풍 특이사항
    if wind >= 8:
        if is_night:
            return ["💨 밤바람이 꽤 강하게 불고 있어요. 창문 단속 잘하시고 따뜻한 밤 보내세요!"]
        return ["💨 바람이 강하게 불어 체감상 더 쌀쌀해요. 바람막이나 겉옷을 꼭 챙기세요!"]

    # 3순위: 시간대별 + 기온별 디테일 멘트
    # [새벽 시간대: 0시 ~ 5시]
    if 0 <= hour < 6:
        if feels_like <= 10:
            return [random.choice([
                f"🌙 새벽 공기가 {temp}°C로 많이 차가워요. 이불 꼭 덮고 따뜻하게 주무세요!",
                "🦉 쌀쌀한 새벽이네요. 아직 깨어 계신다면 따뜻한 물 한 잔 어떠신가요?"
            ])]
        elif feels_like >= 25:
            return ["🌙 열대야처럼 다소 더운 새벽이에요. 쾌적한 온도로 맞춘 뒤 숙면하세요!"]
        else:
            return [random.choice([
                "🌙 고요하고 선선한 새벽이에요. 편안하고 좋은 꿈 꾸시길 바랄게요!",
                "✨ 선선한 밤공기가 기분 좋은 새벽이네요. 푹 쉬고 내일도 힘내세요!",
                "🌙 하루의 끝자락, 차분한 밤공기와 함께 편안한 휴식 시간 보내세요."
            ])]

    # [아침 시간대: 6시 ~ 10시]
    elif 6 <= hour < 11:
        if feels_like <= 8:
            return [f"🧥 아침 기온이 {temp}°C로 꽤 추워요! 든든한 외투 챙겨 입고 활기차게 출발하세요."]
        elif feels_like <= 17:
            return [random.choice([
                "🌤️ 상쾌하지만 살짝 쌀쌀한 아침이에요. 가벼운 겉옷 하나 걸치고 외출하세요!",
                "☕ 선선한 아침 공기가 기분 좋네요! 일교차에 대비해 겉옷을 챙기시면 좋아요."
            ])]
        else:
            return ["☀️ 포근하고 상쾌한 아침입니다! 오늘도 기분 좋은 하루 시작하세요."]

    # [낮 시간대: 11시 ~ 16시]
    elif 11 <= hour < 17:
        if feels_like >= 30:
            return ["🥵 햇볕이 뜨겁고 무더운 낮이에요. 시원한 물 자주 마시며 더위 조심하세요!"]
        elif feels_like >= 24:
            return ["☀️ 조금 덥게 느껴질 수 있는 낮이에요. 가벼운 옷차림으로 시원하게 보내세요!"]
        elif feels_like <= 8:
            return ["🥶 낮에도 바람이 차갑네요. 따뜻한 음료와 함께 포근한 오후 보내세요!"]
        else:
            if "맑음" in desc:
                return [random.choice([
                    "☀️ 햇살이 참 좋은 쾌적한 오후예요! 가볍게 산책하며 기분 전환해 보세요.",
                    "😊 선선하고 맑은 날씨예요. 남은 오후도 기분 좋은 시간 보내세요!"
                ])]
            return ["⛅ 구름이 적당히 낀 선선한 오후네요. 여유롭고 활기찬 낮 시간 보내세요!"]

    # [저녁 시간대: 17시 ~ 20시]
    elif 17 <= hour < 21:
        if feels_like <= 14:
            return [random.choice([
                "🌇 해가 지면서 공기가 금세 차가워졌어요. 퇴근·하교길 따뜻하게 입으세요!",
                "🌆 선선한 저녁 바람이 불어오네요. 감기 걸리지 않게 겉옷 잘 챙기세요!"
            ])]
        else:
            return [random.choice([
                "🌇 선선한 바람이 불어 저녁 산책하기 딱 좋은 날씨예요. 오늘 하루도 고생 많으셨어요!",
                "🍽️ 쾌적한 저녁 날씨네요. 맛있는 저녁 식사와 함께 여유로운 시간 보내세요!"
            ])]

    # [밤 시간대: 21시 ~ 23시]
    else:
        if feels_like <= 12:
            return [random.choice([
                f"🌙 밤기온이 {temp}°C까지 내려가 제법 쌀쌀해요. 창문 잘 닫고 포근한 밤 보내세요!",
                "🍵 밤바람이 차가워요. 오늘 하루도 수고 많으셨고, 따뜻하게 입으시길 바라요!"
            ])]
        else:
            return [random.choice([
                "🌙 선선하고 쾌적한 밤이에요. 오늘 하루도 고생 많으셨습니다, 편안한 밤 보내세요!",
                "✨ 창문 너머로 시원한 밤바람이 불어오네요. 푹 쉬며 하루를 기분 좋게 마무리하세요!"
            ])]

# 💡 2. 하단 탭용 비상 상세 브리핑 생성 함수 (AI 한도 초과 시에도 자연스러운 브리핑 제공)
def make_fallback_briefing(current: dict, forecast: list, air_quality: dict) -> str:
    hour = get_kst_hour()
    temp = round(current.get("temp", 20))
    feels_like = round(current.get("feels_like", temp))
    desc = current.get("description", "맑음")
    wind = current.get("wind_speed", 0)
    pm = air_quality.get("level", "보통") if air_quality else "보통"
    max_pop = int(max((f.get("pop", 0) for f in forecast[:6]), default=0) * 100) if forecast else 0

    if 0 <= hour < 6:
        greeting = "고요한 새벽 시간입니다. 🌙"
        closing = "밤사이 기온이 내려가 다소 쌀쌀할 수 있으니 이불을 잘 덮으시고 편안한 숙면 취하시길 바랍니다."
    elif 6 <= hour < 11:
        greeting = "상쾌한 아침입니다! ☀️"
        closing = "아침저녁으로 일교차가 있을 수 있으니 가벼운 겉옷을 챙겨 활기찬 하루를 시작해 보세요."
    elif 11 <= hour < 17:
        greeting = "활기찬 오후 시간입니다! 🌤️"
        closing = "야외 활동하기 무난한 기온이지만, 체온 유지를 위해 입고 벗기 편한 겉옷을 활용하시는 것을 추천해 드립니다."
    elif 17 <= hour < 21:
        greeting = "하루를 마무리하는 저녁 시간입니다. 🌇"
        closing = "해가 진 뒤에는 바람이 불며 체감 온도가 낮아질 수 있으니 귀갓길 보온에 유의하시길 바랍니다."
    else:
        greeting = "편안한 밤 시간입니다. ✨"
        closing = "오늘 하루도 정말 고생 많으셨습니다. 내일을 위해 따뜻하고 포근한 휴식 시간 보내시길 바랍니다."

    rain_info = f"향후 강수 확률이 {max_pop}%로 예보되어 있으니 외출 시 작은 우산을 챙기시는 것이 좋겠습니다. " if max_pop >= 40 else ""

    return (
        f"{greeting} 현재 하늘은 {desc} 상태이며, 기온은 {temp}°C(체감 {feels_like}°C), 풍속은 {wind}m/s를 기록하고 있습니다. "
        f"미세먼지 농도는 '{pm}' 수준으로 대기 상태를 보이고 있습니다. "
        f"{rain_info}{closing}"
    )

# 💡 3. 메인 AI 호출 및 캐싱 통합 함수
def get_ai_contents(current: dict, forecast: list, air_quality: dict) -> dict:
    fallback_one_line = " ".join(make_advice(current, forecast))
    fallback_briefing = make_fallback_briefing(current, forecast, air_quality)
    
    if not GEMINI_API_KEY:
        return {
            "one_line": fallback_one_line,
            "briefing": fallback_briefing
        }

    # 캐시 키 생성 (도시명 + 현재 시간대 + 날씨 설명 + 온도 기준)
    hour = get_kst_hour()
    city = current.get("city", "default")
    temp = round(current.get("temp", 20))
    desc = current.get("description", "맑음")
    cache_key = f"{city}_{hour}_{desc}_{temp}"

    # 30분 이내에 동일한 조건으로 생성된 AI 응답이 있다면 API 호출 없이 즉시 반환!
    now_ts = time.time()
    if cache_key in AI_CACHE:
        saved_time, saved_data = AI_CACHE[cache_key]
        if now_ts - saved_time < CACHE_TTL:
            return saved_data

    feels_like = round(current.get("feels_like", temp))
    wind = current.get("wind_speed", 0)
    pm = air_quality.get("level", "보통") if air_quality else "보통"
    max_pop = int(max((f.get("pop", 0) for f in forecast[:6]), default=0) * 100) if forecast else 0
    
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
        # 💡 불필요한 모델 목록 조회 통신(1~2초) 삭제 후 빠르고 강력한 최신 모델 직접 지정
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
            AI_CACHE[cache_key] = (now_ts, result)  # 성공한 응답을 캐시에 저장
            return result
        else:
            return {"one_line": fallback_one_line, "briefing": fallback_briefing}
            
    except Exception:
        # 💡 429 할당량 초과나 에러 발생 시 에러 문구 대신 완성도 높은 상황별 기본 멘트&브리핑 반환!
        return {
            "one_line": fallback_one_line, 
            "briefing": fallback_briefing
        }