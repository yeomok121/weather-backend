import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

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

def get_ai_briefing(current: dict, air_quality: dict) -> str:
    if not GEMINI_API_KEY:
        return "🔑 백엔드의 .env 파일과 Render에 'GEMINI_API_KEY'를 추가하시면 AI 맞춤형 브리핑이 활성화됩니다!"
    
    temp = current.get("temp", 20)
    feels_like = current.get("feels_like", 20)
    desc = current.get("description", "맑음")
    wind = current.get("wind_speed", 0)
    pm = air_quality.get("level", "보통") if air_quality else "보통"
    
    prompt = (
        f"현재 날씨는 {desc}, 기온은 {temp}도(체감 {feels_like}도), 풍속은 {wind}m/s, 미세먼지는 {pm} 수준입니다. "
        "이 데이터를 바탕으로 기상캐스터처럼 다정하고 전문적인 날씨 브리핑을 3~4문장으로 작성해줘. "
        "외출 시 옷차림 조언이나 주의사항을 포함하고, 마크다운(***) 없이 평문으로 쓰되 이모지를 적절히 섞어줘."
    )
    
    try:
        # 💡 공식 표준 모델인 gemini-1.5-flash 지정
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        
        if response and response.text:
            return response.text.strip()
        else:
            return "AI 응답을 받아오지 못했습니다."
            
    except Exception as e:
        return f"❌ AI 에러 발생: {str(e)}"