"""앱 진입점: FastAPI 앱 생성, 공통 미들웨어·오류 처리, 라우터 등록만 담당합니다.

기능별 API는 app/routers/ 아래 파일에 있습니다.
  weather.py  날씨 페이지      mountain.py, hiking.py  산악 페이지
  fishing.py  낚시 페이지      admin.py, notices.py    관리자 페이지
  auth.py     회원(공통)
새 라우터를 만들면 아래 include_router 목록에 한 줄 추가해 주세요.
"""
import asyncio
from contextlib import asynccontextmanager, suppress

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymysql import err as db_errors

from app.health import router as health_router, monitor_database
from app.weather_diagnostics import RequestLogMiddleware, log_environment_status, log_error, request_id_var
from app.routers import admin, auth, fishing, hiking, mountain, notices, weather

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Track dependency recovery without blocking HTTP startup.
    monitor_task = asyncio.create_task(monitor_database())
    try:
        yield
    finally:
        monitor_task.cancel()
        with suppress(asyncio.CancelledError):
            await monitor_task


app = FastAPI(lifespan=lifespan)
app.include_router(health_router)
log_environment_status()


@app.exception_handler(db_errors.OperationalError)
@app.exception_handler(db_errors.InterfaceError)
async def database_unavailable_handler(request, exc):
    log_error("database_unavailable", exc)
    return JSONResponse(status_code=503, headers={"Retry-After": "30"}, content={
        "detail": "회원 정보 서버에 연결하지 못했습니다. 작성한 내용은 유지됩니다. 잠시 후 다시 시도해 주세요.",
        "request_id": request_id_var.get(),
    })


@app.exception_handler(db_errors.IntegrityError)
async def database_conflict_handler(request, exc):
    log_error("database_conflict", exc)
    return JSONResponse(status_code=409, content={"detail": "이미 등록된 항목이거나 변경된 자료입니다. 새로고침한 뒤 다시 확인해 주세요."})


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RequestLogMiddleware)


@app.get("/")
def hello():
    return {"message": "날씨 서버 작동 중!"}


# 등록 순서는 기존 main.py의 API 순서와 같게 유지합니다.
app.include_router(weather.router)
app.include_router(mountain.router)
app.include_router(fishing.router)
app.include_router(auth.router)
app.include_router(hiking.router)
app.include_router(admin.router)
app.include_router(notices.router)
