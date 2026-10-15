import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# 비상용 기본 한 줄 멘트 함수 (AI 키가 없거나 오류 시 작동)
def make_advice(current: dict, forecast: list) -> list:
    tips = []
    temp = current.get("temp", 20)
    feels_like = current.get("feels_like", 20)
    wind = current.get("wind_speed", 0)
    max_pop = max((f.get("pop", 0) for f in forecast), default=0)

    if max_pop >= 0.5:
        tips.append("☔ 비 올 확률이 높아요. 우산을 챙기세요.")
    if feels_like >= 33:
        tips.append("🥵 체감온도가 매우 높아요. 물을 자주 드세요.")
    elif feels_like <= 5:
        tips.append("🥶 매우 추워요. 두꺼운 외투를 입으세요.")
    if wind >= 9:
        tips.append("💨 바람이 강해요. 외출 시 주의하세요.")
    if current.get("icon") == "01d":
        tips.append("☀️ 햇빛이 강해요. 양산이나 선크림을 추천해요.")
    if not tips:
        tips.append("😊 선선하고 쾌적한 날씨예요. 기분 좋은 하루 보내세요!")
        
    return tips

# 💡 한 줄 멘트와 상세 브리핑을 동시에 생성하는 함수
def get_ai_contents(current: dict, forecast: list, air_quality: dict) -> dict:
    fallback_advice = " ".join(make_advice(current, forecast))
    
    if not GEMINI_API_KEY:
        return {
            "one_line": fallback_advice,
            "briefing": "🔑 백엔드의 .env 파일과 Render에 'GEMINI_API_KEY'를 추가하시면 AI 맞춤형 브리핑이 활성화됩니다!"
        }
    
    temp = current.get("temp", 20)
    feels_like = current.get("feels_like", 20)
    desc = current.get("description", "맑음")
    wind = current.get("wind_speed", 0)
    pm = air_quality.get("level", "보통") if air_quality else "보통"
    max_pop = int(max((f.get("pop", 0) for f in forecast), default=0) * 100)
    
    prompt = (
        f"현재 날씨는 {desc}, 기온은 {temp}도(체감 {feels_like}도), 풍속은 {wind}m/s, 미세먼지는 {pm}, 강수확률은 {max_pop}%입니다.\n"
        "아래 두 가지 항목을 작성해주세요. 마크다운(**, ## 등)은 절대 사용하지 말고 평문과 이모지만 사용하세요.\n\n"
        "1. 한줄멘트: 현재 날씨 상황에 딱 맞는 센스 있고 다정한 추천 멘트 딱 한 문장 (예: 🌤️ 선선한 바람이 불어 산책하기 딱 좋은 하루예요!)\n"
        "2. 상세브리핑: 기상캐스터처럼 다정하고 전문적인 날씨 브리핑 3~4문장 (외출 시 옷차림 조언이나 주의사항 포함)\n\n"
        "반드시 아래 출력 형식을 정확히 지켜서 답변해주세요:\n"
        "한줄: [여기에 한줄멘트 작성]\n"
        "상세: [여기에 상세브리핑 작성]"
    )
    
    try:
        available_models = [
            m.name for m in genai.list_models() 
            if 'generateContent' in m.supported_generation_methods
        ]
        
        if not available_models:
            return {"one_line": fallback_advice, "briefing": "❌ 사용 가능한 AI 모델이 없습니다."}

        target_model = available_models[0]
        for m in available_models:
            if '3' in m and 'flash' in m:
                target_model = m
                break
            elif 'flash' in m:
                target_model = m

        model = genai.GenerativeModel(target_model)
        response = model.generate_content(prompt)
        
        if response and response.text:
            text = response.text.strip()
            one_line = fallback_advice
            briefing = text
            
            # AI 응답에서 '한줄:'과 '상세:'를 분리하여 추출
            if "한줄:" in text and "상세:" in text:
                parts = text.split("상세:")
                one_line = parts[0].replace("한줄:", "").strip()
                briefing = parts[1].strip()
            else:
                lines = [line.strip() for line in text.split("\n") if line.strip()]
                if len(lines) >= 2:
                    one_line = lines[0].replace("한줄:", "").strip()
                    briefing = " ".join(lines[1:]).replace("상세:", "").strip()
                    
            return {"one_line": one_line, "briefing": briefing}
        else:
            return {"one_line": fallback_advice, "briefing": "AI 응답을 받아오지 못했습니다."}
            
    except Exception as e:
        error_str = str(e).lower()
        if "429" in error_str or "quota" in error_str:
            return {
                "one_line": fallback_advice, 
                "briefing": "오늘 AI가 너무 많은 날씨를 분석해서 잠시 휴식 중이에요! 💤 내일 다시 다정하고 똑똑한 브리핑으로 돌아올게요. (현재 기본 예보 정보는 정상 제공 중입니다)"
            }
        return {"one_line": fallback_advice, "briefing": f"❌ AI 에러 발생: {str(e)}"}