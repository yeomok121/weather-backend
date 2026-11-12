from fastapi import APIRouter
from fastapi.responses import JSONResponse
from app import wind

router = APIRouter(prefix="/api/weather/wind", tags=["wind"])


@router.get("/stations")
def stations():
    try:
        return wind.summary()
    except wind.WindError as error:
        return JSONResponse(status_code=503, content={"detail": str(error), "error_code": error.code},
                            headers={"Retry-After": "30", "Cache-Control": "no-store"})
