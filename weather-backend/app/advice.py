def make_advice(current: dict, forecast: list) -> list:
    """현재 날씨와 예보를 보고 행동요령 문구 목록을 만든다."""
    tips = []

    # 앞으로의 최대 강수확률
    max_pop = max((f["pop"] for f in forecast), default=0)
    if max_pop >= 0.5:
        tips.append("☔ 비 올 확률이 높아요. 우산을 챙기세요.")

    if current["feels_like"] >= 33:
        tips.append("🥵 체감온도가 매우 높아요. 물을 자주 드세요.")
    elif current["feels_like"] <= 0:
        tips.append("🥶 매우 추워요. 두꺼운 외투를 입으세요.")

    if current["wind_speed"] >= 10:
        tips.append("💨 바람이 강해요. 외출 시 주의하세요.")

    # 맑은 날(아이콘 01d = 낮에 맑음)
    if current["icon"] == "01d":
        tips.append("☀️ 햇빛이 강해요. 양산이나 선크림을 추천해요.")

    if not tips:
        tips.append("😊 오늘은 특별한 주의사항이 없어요.")
    return tips
