"""Public radar observations; provider credentials stay on the server."""
from fastapi import APIRouter, HTTPException, Response
from app import radar

router = APIRouter(prefix="/api/weather/radar", tags=["radar"])


@router.get("/frames")
def frames():
    try:
        return radar.manifest()
    except radar.RadarError as error:
        raise HTTPException(status_code=error.status, detail=str(error),
                            headers={"Retry-After": "30"}) from None


@router.get("/frames/{stamp}.png")
def image(stamp: str):
    try:
        content = radar.frame(stamp)
    except radar.RadarError as error:
        raise HTTPException(status_code=error.status, detail=str(error)) from None
    return Response(content=content, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})
