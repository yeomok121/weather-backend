"""Public radar observations; provider credentials stay on the server."""
from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from app import radar

router = APIRouter(prefix="/api/weather/radar", tags=["radar"])


@router.get("/frames")
def frames():
    try:
        return radar.manifest()
    except radar.RadarError as error:
        return JSONResponse(status_code=error.status, content={"detail": str(error), "error_code": error.code},
                            headers={"Retry-After": "30", "Cache-Control": "no-store"})


@router.get("/frames/{stamp}.png")
def image(stamp: str):
    try:
        content = radar.frame(stamp)
    except radar.RadarError as error:
        return JSONResponse(status_code=error.status, content={"detail": str(error), "error_code": error.code},
                            headers={"Retry-After": "30", "Cache-Control": "no-store"})
    return Response(content=content, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})
