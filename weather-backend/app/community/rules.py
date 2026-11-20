"""낚시 커뮤니티: 글 입력 검사, 사진 처리, 권한 (DB 없이 시험할 수 있는 부분).

용도: 사진으로 조과 자랑(자랑), 낚시 팁 공유(팁). 공지는 관리자만 씁니다.
"""
import base64
import io
import re

from PIL import Image, ImageOps

CATEGORIES = ("자랑", "팁", "공지")
WRITER_CATEGORIES = ("자랑", "팁")             # 일반 회원이 쓸 수 있는 분류
NOTICE_ROLES = {"superadmin", "notice_manager"}  # 공지를 쓸 수 있는 역할
MODERATOR_ROLES = {"superadmin", "member_manager", "notice_manager"}  # 남의 글을 지울 수 있는 역할

MAX_TITLE = 60
MAX_BODY = 2000
MAX_PHOTOS = 3
MAX_UPLOAD_BYTES = 6 * 1024 * 1024     # 받은 원본(브라우저에서 이미 줄여 보냄) 한 장 한도
MAX_EDGE = 1280                        # 저장할 사진의 긴 변
TARGET_BYTES = 450 * 1024              # 저장할 사진 한 장 목표 크기
MAX_PIXELS = 40_000_000                # 압축 폭탄 방지
DAILY_POST_LIMIT = 10                  # 한 사람이 하루에 쓸 수 있는 글
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # 줄바꿈(\n)·탭은 남김


def _text(value, limit, label, required=True, multiline=False):
    text = CONTROL.sub("", str(value or ""))
    text = text.replace("\r\n", "\n").strip() if multiline else " ".join(text.split())
    if required and not text:
        raise ValueError(f"{label}을(를) 입력해 주세요.")
    if len(text) > limit:
        raise ValueError(f"{label}은(는) {limit}자까지 쓸 수 있어요.")
    return text


def clean_post(data: dict, role: str = "member") -> dict:
    """글 입력값 정리·검사. 잘못되면 ValueError(사용자용 문구), 권한 문제면 PermissionError."""
    category = str(data.get("category") or "")
    if category not in CATEGORIES:
        raise ValueError("분류를 골라 주세요.")
    if category == "공지" and role not in NOTICE_ROLES:
        raise PermissionError("공지는 관리자만 쓸 수 있어요.")
    photos = data.get("photos") or []
    if not isinstance(photos, list) or len(photos) > MAX_PHOTOS:
        raise ValueError(f"사진은 {MAX_PHOTOS}장까지 올릴 수 있어요.")
    if category == "자랑" and not photos:
        raise ValueError("자랑 글에는 사진을 한 장 이상 올려 주세요.")
    length = data.get("length_cm")
    if length in ("", None):
        length = None
    else:
        try:
            length = round(float(length), 1)
        except (TypeError, ValueError):
            raise ValueError("길이는 숫자로 입력해 주세요.") from None
        if not 0 < length < 500:
            raise ValueError("길이를 확인해 주세요.")
    return {
        "category": category,
        "title": _text(data.get("title"), MAX_TITLE, "제목"),
        "body": _text(data.get("body"), MAX_BODY, "내용", required=category != "자랑", multiline=True),
        "point_name": _text(data.get("point_name"), 80, "포인트", required=False) or None,
        "species": _text(data.get("species"), 30, "어종", required=False) or None,
        "length_cm": length if category == "자랑" else None,
        "photos": photos,
    }


def process_photo(data_url_or_b64: str) -> dict:
    """사진 한 장: 그림 파일인지 확인 → 방향 바로잡기 → 위치정보(EXIF) 등 메타데이터 제거 → 긴 변 1280px JPEG로 다시 저장."""
    value = str(data_url_or_b64 or "")
    if value.startswith("data:"):
        value = value.split(",", 1)[-1]
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise ValueError("사진 파일을 읽지 못했어요.") from None
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("사진 한 장이 너무 커요. 6MB 이하로 올려 주세요.")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            if probe.format not in ("JPEG", "PNG", "WEBP"):
                raise ValueError("JPG·PNG·WEBP 사진만 올릴 수 있어요.")
            probe.verify()
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source)
            image = image.convert("RGB")  # 투명도·메타데이터는 버림
            image.thumbnail((MAX_EDGE, MAX_EDGE))
    except ValueError:
        raise
    except Exception:  # noqa: BLE001 - 깨진 파일·압축 폭탄 등
        raise ValueError("사진 파일을 읽지 못했어요.") from None
    for quality in (82, 72, 62):
        out = io.BytesIO()
        image.save(out, "JPEG", quality=quality, optimize=True)  # exif를 넘기지 않으므로 위치정보가 남지 않음
        if out.tell() <= TARGET_BYTES:
            break
    return {"bytes": out.getvalue(), "mime": "image/jpeg", "width": image.width, "height": image.height}


def can_delete(post_user_id: int, user: dict) -> bool:
    return user.get("id") == post_user_id or user.get("role") in MODERATOR_ROLES
