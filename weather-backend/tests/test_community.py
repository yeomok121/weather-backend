"""낚시 커뮤니티: 입력 검사·사진 처리·권한 (DB 없이). 실제 SQL은 로컬 MariaDB로 따로 확인했습니다."""
import base64
import io
import unittest

from PIL import Image

from app.community import rules


def jpeg_with_gps(size=(3000, 2000), orientation=None):
    """휴대폰 사진처럼 위치정보(GPS)와 방향 정보가 들어 있는 JPEG"""
    image = Image.new("RGB", size, (30, 120, 160))
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (33.0, 57.0, 15.0), 3: "E", 4: (126.0, 18.0, 39.0)}  # GPSInfo: 추자도 근처
    exif[0x010F] = "PhoneMaker"
    if orientation:
        exif[0x0112] = orientation
    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif, quality=95)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


class CleanPostTests(unittest.TestCase):
    def test_brag_needs_photo_and_keeps_length(self):
        with self.assertRaisesRegex(ValueError, "사진"):
            rules.clean_post({"category": "자랑", "title": "감성돔"})
        post = rules.clean_post({"category": "자랑", "title": "  감성돔   한 마리 ", "photos": ["x"], "length_cm": "32.25", "species": "감성돔"})
        self.assertEqual((post["title"], post["length_cm"], post["body"]), ("감성돔 한 마리", 32.2, ""))

    def test_tip_needs_body_and_limits(self):
        with self.assertRaisesRegex(ValueError, "내용"):
            rules.clean_post({"category": "팁", "title": "채비"})
        with self.assertRaisesRegex(ValueError, "60자"):
            rules.clean_post({"category": "팁", "title": "가" * 61, "body": "x"})
        with self.assertRaisesRegex(ValueError, "3장"):
            rules.clean_post({"category": "팁", "title": "t", "body": "b", "photos": ["a", "b", "c", "d"]})
        tip = rules.clean_post({"category": "팁", "title": "t", "body": "첫 줄\r\n둘째\x07 줄", "length_cm": 30})
        self.assertEqual((tip["body"], tip["length_cm"]), ("첫 줄\n둘째 줄", None))  # 줄바꿈은 남기고 제어문자 제거, 팁에는 길이 없음

    def test_only_admins_write_notices(self):
        with self.assertRaises(PermissionError):
            rules.clean_post({"category": "공지", "title": "규칙", "body": "b"}, "member")
        with self.assertRaises(PermissionError):
            rules.clean_post({"category": "공지", "title": "규칙", "body": "b"}, "member_manager")
        self.assertEqual(rules.clean_post({"category": "공지", "title": "규칙", "body": "b"}, "notice_manager")["category"], "공지")
        with self.assertRaisesRegex(ValueError, "분류"):
            rules.clean_post({"category": "잡담", "title": "t", "body": "b"})

    def test_delete_permission(self):
        self.assertTrue(rules.can_delete(7, {"id": 7, "role": "member"}))
        self.assertFalse(rules.can_delete(7, {"id": 8, "role": "member"}))
        self.assertTrue(rules.can_delete(7, {"id": 8, "role": "member_manager"}))


class PhotoTests(unittest.TestCase):
    def test_strips_location_and_resizes(self):
        photo = rules.process_photo(jpeg_with_gps())
        saved = Image.open(io.BytesIO(photo["bytes"]))
        self.assertEqual(saved.format, "JPEG")
        self.assertEqual(max(saved.size), 1280)
        self.assertEqual((photo["width"], photo["height"]), saved.size)
        self.assertEqual(dict(saved.getexif()), {})  # 위치정보·기기 정보 없음
        self.assertLessEqual(len(photo["bytes"]), rules.TARGET_BYTES)

    def test_applies_orientation_before_dropping_exif(self):
        photo = rules.process_photo(jpeg_with_gps(size=(1600, 900), orientation=6))  # 세로로 찍은 사진
        self.assertEqual((photo["width"], photo["height"]), (720, 1280))

    def test_rejects_non_images_and_other_formats(self):
        with self.assertRaisesRegex(ValueError, "읽지 못"):
            rules.process_photo(base64.b64encode(b"<script>alert(1)</script>").decode())
        with self.assertRaisesRegex(ValueError, "읽지 못"):
            rules.process_photo("not base64!!")
        gif = io.BytesIO()
        Image.new("RGB", (10, 10)).save(gif, "GIF")
        with self.assertRaisesRegex(ValueError, "JPG"):
            rules.process_photo(base64.b64encode(gif.getvalue()).decode())


class CommentTests(unittest.TestCase):
    def test_clean_comment(self):
        self.assertEqual(rules.clean_comment('  좋아요!\r\n감사\x07해요 '), '좋아요!\n감사해요')
        with self.assertRaisesRegex(ValueError, "댓글"):
            rules.clean_comment('   ')
        with self.assertRaisesRegex(ValueError, "500자"):
            rules.clean_comment('가' * 501)
