import io
import struct
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import numpy as np
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import radar
from app.routers.radar import router


def binary(values, kind="echo", endian="<"):
    values = np.array(values, dtype=f"{endian}{'i2' if kind == 'echo' else 'f4'}")
    ny, nx = values.shape
    return struct.pack(f"{endian}hh", nx, ny) + values.tobytes()


class RadarTests(unittest.TestCase):
    def setUp(self):
        radar._lookup = None
        radar._frames.clear()
        radar._failures.clear()
        radar._manifest = None
        radar._manifest_until = 0
        radar._retry_after = 0

    def test_binary_endian_coordinates_and_errors(self):
        for endian in ("<", ">"):
            grid = radar.decode_grid(binary([[0, 2000], [-25000, -30000]], endian=endian), "echo")
            self.assertEqual(grid[0, 1], 2000)
            coords = radar.decode_grid(binary([[37, 37], [38, 38]], "lat", endian), "lat")
            self.assertEqual(coords[1, 0], 38)
        for content in (b"", b"<html>Access denied</html>", binary([[0, 0], [0, 0]])[:-1]):
            with self.assertRaises(radar.RadarError):
                radar.decode_grid(content, "echo")

    def test_projection_north_is_up_and_east_is_right(self):
        coords = {
            "lon": binary([[120, 121, 132, 133]] * 4, "lon"),
            "lat": binary([[32] * 4, [33] * 4, [42] * 4, [43] * 4], "lat"),
        }
        with patch.object(radar, "_fetch", side_effect=lambda path, **params: coords[params["latlon"]]):
            valid, pixels = radar._pixel_lookup((4, 4))
        self.assertTrue(valid.all())
        self.assertLess(pixels[0] % radar.SIZE, pixels[1] % radar.SIZE)
        self.assertGreater(pixels[0] // radar.SIZE, pixels[2] // radar.SIZE)

    def test_missing_is_distinct_from_no_echo_and_echo(self):
        grid = np.zeros((4, 4), dtype=np.int16)
        grid[0, 0], grid[0, 2], grid[2, 0], grid[2, 2] = -30000, -25000, 2000, 5000
        image = radar.render_grid(grid, np.ones((2, 2), dtype=bool), np.array([0, 1, 2, 3]))
        pixels = np.array(Image.open(io.BytesIO(image))).reshape(-1, 4)
        self.assertGreater(pixels[0, 3], 0)
        self.assertEqual(pixels[1, 3], 0)
        self.assertEqual(tuple(pixels[2, :3]), (55, 191, 100))
        self.assertEqual(tuple(pixels[3, :3]), (217, 67, 87))

    def test_cache_and_public_validation(self):
        stamp = (datetime.now(radar.KST) - timedelta(minutes=10)).replace(second=0, microsecond=0)
        stamp -= timedelta(minutes=stamp.minute % 5)
        with patch.object(radar, "_fetch", return_value=binary([[0, 1000], [2000, 3000]])) as fetch:
            with patch.object(radar, "_pixel_lookup", return_value=(np.ones((1, 1), dtype=bool), np.array([0]))):
                first = radar.frame(stamp.strftime("%Y%m%d%H%M"))
                self.assertEqual(first, radar.frame(stamp.strftime("%Y%m%d%H%M")))
        self.assertEqual(fetch.call_count, 1)
        for bad in ("not-a-date", "209901010000", "202001010000"):
            with self.assertRaises(radar.RadarError):
                radar.frame(bad)

    def test_manifest_times_and_shared_failure_cooldown(self):
        with patch.object(radar, "_frame", return_value=b"png") as frame:
            data = radar.manifest()
            self.assertEqual(data, radar.manifest())
        self.assertEqual(frame.call_count, 1)
        self.assertEqual(len(data["frames"]), 7)
        first = datetime.fromisoformat(data["frames"][0]["observed_at"])
        latest = datetime.fromisoformat(data["latest_at"])
        self.assertEqual(latest - first, timedelta(hours=1))
        self.assertLess(latest, datetime.now(radar.KST))
        radar._manifest = None
        with patch.object(radar, "_frame", side_effect=radar.RadarError("permission", 503)) as frame:
            for _ in range(2):
                with self.assertRaises(radar.RadarError):
                    radar.manifest()
        self.assertEqual(frame.call_count, 1)

    def test_router_status_and_png_headers(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        with patch.object(radar, "manifest", side_effect=radar.RadarError("승인 확인 필요", 503)):
            response = client.get("/api/weather/radar/frames")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["retry-after"], "30")
        self.assertEqual(response.headers["cache-control"], "no-store")
        with patch.object(radar, "frame", return_value=b"png"):
            response = client.get("/api/weather/radar/frames/202611120900.png")
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertIn("max-age=3600", response.headers["cache-control"])

    def test_delayed_latest_falls_back_and_reports_actual_time(self):
        missing = radar.RadarError("not published", 502, "no_data")
        with patch.object(radar, "_frame", side_effect=[missing, missing, b"png"]) as frame:
            data = radar.manifest()
        self.assertEqual(frame.call_count, 3)
        latest_requested = frame.call_args_list[-1].args[0]
        self.assertEqual(data["frames"][-1]["id"], latest_requested)
        self.assertEqual(datetime.fromisoformat(data["latest_at"]).strftime("%Y%m%d%H%M"), latest_requested)

    def test_forbidden_is_not_retried_and_code_survives_cooldown(self):
        with patch.object(radar, "_frame", side_effect=radar.RadarError("access denied", 503, "provider_forbidden")) as frame:
            for _ in range(2):
                with self.assertRaises(radar.RadarError) as caught:
                    radar.manifest()
                self.assertEqual(caught.exception.code, "provider_forbidden")
        self.assertEqual(frame.call_count, 1)

    def test_minimum_reflectivity_is_transparent_and_not_missing(self):
        grid = np.full((2, 2), -20000, dtype=np.int16)
        image = radar.render_grid(grid, np.ones((1, 1), dtype=bool), np.array([0]))
        pixel = Image.open(io.BytesIO(image)).getpixel((0, 0))
        self.assertEqual(pixel[3], 0)


if __name__ == "__main__":
    unittest.main()
