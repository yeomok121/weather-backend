def make_advice(current, forecast):
    # 1. 예보 데이터(forecast)에서 실제 리스트 배열을 안전하게 추출
    if isinstance(forecast, dict) and "list" in forecast:
        forecast_data = forecast["list"]
    elif isinstance(forecast, list):
        forecast_data = forecast
    else:
        forecast_data = []

    # 2. 강수 확률(pop) 계산 (기상청 API에는 pop이 없으므로 안전하게 0으로 처리되도록 방어 코드 추가)
    max_pop = 0
    for f in forecast_data:
        if isinstance(f, dict):
            # 값이 없으면 0을 반환하여 에러 방지
            pop = f.get("pop", 0) 
            if pop > max_pop:
                max_pop = pop

    # 3. 현재 온도와 날씨 상태 추출
    try:
        current_temp = current.get("main", {}).get("temp", 20) # 온도가 없으면 기본값 20
        weather_desc = current.get("weather", [{}])[0].get("description", "맑음")
    except Exception:
        current_temp = 20
        weather_desc = "맑음"

    # 4. 맞춤형 날씨 조언 문구 생성
    if "비" in weather_desc:
        return "현재 비가 내리고 있습니다. 우산을 꼭 챙기시고 미끄럼에 주의하세요!"
    elif "눈" in weather_desc:
        return "현재 눈이 내리고 있습니다. 빙판길 안전에 유의하세요."
    
    # 강수 확률이 높을 때 (OWM 기준 0.5 이상 또는 기상청 기준 50 이상)
    if max_pop >= 0.5 or max_pop >= 50:
        return "오늘 비나 눈이 올 확률이 높습니다. 외출 시 우산을 챙기시는 것이 좋습니다."
    
    # 온도에 따른 조언
    if current_temp >= 28:
        return "날씨가 많이 덥습니다. 자외선 차단에 신경 쓰고 수분을 충분히 섭취하세요."
    elif current_temp >= 15:
        return "야외 활동하기 좋은 날씨입니다! 쾌적한 하루 보내세요."
    elif current_temp >= 5:
        return "일교차가 있거나 다소 쌀쌀합니다. 따뜻한 겉옷을 챙기세요."
    else:
        return "날씨가 매우 춥습니다. 감기에 걸리지 않도록 따뜻하게 입으세요."