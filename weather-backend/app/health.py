"""Fast liveness and cached dependency health; no credentials in responses."""
import asyncio
import time
from datetime import datetime, timezone
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from app.database import get_conn
from app.weather_diagnostics import emit

router = APIRouter()
started_at = time.monotonic()
database_state = {"status": "checking", "checked_at": None}


def probe_database():
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return "available"
    except Exception:
        return "unavailable"
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


async def monitor_database():
    while True:
        status = await asyncio.to_thread(probe_database)
        if status != database_state["status"]:
            emit("dependency_status_changed", dependency="database", status=status)
        database_state.update(status=status, checked_at=datetime.now(timezone.utc).isoformat())
        await asyncio.sleep(60)


@router.get("/api/health/live")
async def liveness():
    return {"status": "ok", "uptime_seconds": round(time.monotonic() - started_at)}


@router.get("/api/health")
async def dependency_health():
    available = database_state["status"] == "available"
    return JSONResponse(status_code=200 if available else 503, headers={"Cache-Control": "no-store", "Retry-After": "30"}, content={
        "status": "ok" if available else "degraded",
        "database": dict(database_state),
        "weather": "independent_of_database",
        "message": "서비스가 정상입니다" if available else "회원 정보 연결을 확인 중입니다. 날씨 조회는 계속 이용할 수 있습니다.",
    })
