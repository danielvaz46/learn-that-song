"""Unit tests for the pure logic in worker.py. Run from the project root: python worker/test_worker.py"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Load by path: a plain `import worker` would pick up the worker/ folder when run from the project root.
_spec = importlib.util.spec_from_file_location("worker_mod", Path(__file__).with_name("worker.py"))
worker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(worker)


def info(**kw):
    base = {"duration": 240, "categories": ["Music"], "is_live": False, "live_status": "not_live"}
    base.update(kw)
    return base


class ParseMessage(unittest.TestCase):
    def test_valid(self):
        body = json.dumps({"job_id": "abc12345-def", "video_id": "3deDNMr12rQ"})
        self.assertEqual(worker.parse_message(body), ("abc12345-def", "3deDNMr12rQ"))

    def test_rejects_bad_input(self):
        bad = [
            "not json",
            json.dumps({}),
            json.dumps({"job_id": "abc12345", "video_id": "short"}),
            json.dumps({"job_id": "abc12345", "video_id": "3deDNMr12rQ; rm -rf /"}),
            json.dumps({"job_id": "../etc", "video_id": "3deDNMr12rQ"}),
            json.dumps({"job_id": "abc12345", "video_id": 12345678901}),
            json.dumps([1, 2]),
        ]
        for body in bad:
            self.assertIsNone(worker.parse_message(body), body)


class ValidateInfo(unittest.TestCase):
    def code(self, i):
        with self.assertRaises(worker.JobError) as cm:
            worker.validate_info(i)
        return cm.exception.code

    def test_accepts_music(self):
        worker.validate_info(info())

    def test_accepts_by_artist_or_track_metadata(self):
        worker.validate_info(info(categories=["Entertainment"], artist="Slipknot"))
        worker.validate_info(info(categories=[], track="Three Nil"))

    def test_limits(self):
        worker.validate_info(info(duration=30))
        worker.validate_info(info(duration=480))
        self.assertEqual(self.code(info(duration=29)), "too_short")
        self.assertEqual(self.code(info(duration=481)), "too_long")
        self.assertEqual(self.code(info(duration=None)), "unavailable")

    def test_live(self):
        self.assertEqual(self.code(info(is_live=True)), "live")
        self.assertEqual(self.code(info(live_status="is_upcoming")), "live")

    def test_not_music(self):
        self.assertEqual(self.code(info(categories=["Gaming"])), "not_music")
        self.assertEqual(self.code(info(categories=None)), "not_music")


class ClassifyError(unittest.TestCase):
    def test_codes(self):
        c = worker.classify_ytdlp_error
        self.assertEqual(c("ERROR: Sign in to confirm you're not a bot").code, "service_unavailable")
        self.assertEqual(c("ERROR: Video unavailable").code, "unavailable")
        self.assertEqual(c("ERROR: Private video").code, "unavailable")
        self.assertEqual(c("something else").code, "extract_failed")

    def test_user_message_hides_internals(self):
        msg = worker.classify_ytdlp_error("Sign in to confirm you're not a bot").message
        self.assertNotIn("cookie", msg.lower())


class Cache(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patch = mock.patch.object(worker, "CACHE_DIR", self.tmp / "cache")
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def make_stems(self, name):
        d = self.tmp / name
        d.mkdir()
        for s in worker.STEMS:
            (d / f"{s}.opus").write_bytes(b"x" * 100)
        return d

    def test_roundtrip(self):
        self.assertIsNone(worker.cache_get("3deDNMr12rQ"))
        worker.cache_put("3deDNMr12rQ", self.make_stems("src"), {"name": "Song"})
        d, meta = worker.cache_get("3deDNMr12rQ")
        self.assertEqual(meta["name"], "Song")
        self.assertTrue((d / "guitar.opus").exists())

    def test_incomplete_cache_is_a_miss(self):
        worker.cache_put("3deDNMr12rQ", self.make_stems("src"), {"name": "Song"})
        (worker.CACHE_DIR / "3deDNMr12rQ" / "drums.opus").unlink()
        self.assertIsNone(worker.cache_get("3deDNMr12rQ"))

    def test_eviction_removes_oldest(self):
        with mock.patch.object(worker, "CACHE_LIMIT_BYTES", 1000):  # 6 stems*100 + meta ~ 650 per song
            for i, vid in enumerate(["aaaaaaaaaaa", "bbbbbbbbbbb"]):
                worker.cache_put(vid, self.make_stems(f"s{i}"), {"name": vid})
                os.utime(worker.CACHE_DIR / vid, (1000 + i, 1000 + i))
            self.assertIsNone(worker.cache_get("aaaaaaaaaaa"))
            self.assertIsNotNone(worker.cache_get("bbbbbbbbbbb"))


class UploadStems(unittest.TestCase):
    def test_meta_written_after_all_stems(self):
        tmp = Path(tempfile.mkdtemp())
        for s in worker.STEMS:
            (tmp / f"{s}.opus").write_bytes(b"x")
        s3 = mock.Mock()
        order = []
        s3.upload_file.side_effect = lambda *a, **k: order.append("stem")
        s3.put_object.side_effect = lambda **k: order.append(k["Key"])
        worker.upload_stems(s3, "3deDNMr12rQ", tmp, {"name": "Song"})
        self.assertEqual(order[:6], ["stem"] * 6)
        self.assertEqual(order[-1], "stems/3deDNMr12rQ/meta.json")
        self.assertEqual(json.loads(s3.put_object.call_args.kwargs["Body"]), {"name": "Song"})


class ProgressWrites(unittest.TestCase):
    def test_throttle_and_forced_writes(self):
        s3 = mock.Mock()
        p = worker.Progress(s3, "abc12345", "3deDNMr12rQ")
        p.update("downloading", 8, "dl", force=True)       # status change -> write
        p.update(percent=9)                                # throttled
        self.assertEqual(s3.put_object.call_count, 1)
        p.update("done", 100, force=True)
        self.assertEqual(s3.put_object.call_count, 2)
        body = json.loads(s3.put_object.call_args.kwargs["Body"])
        self.assertEqual((body["status"], body["percent"]), ("done", 100))
        self.assertEqual(s3.put_object.call_args.kwargs["Key"], "progress/abc12345.json")

    def test_s3_failure_does_not_raise(self):
        s3 = mock.Mock()
        s3.put_object.side_effect = RuntimeError("boom")
        worker.Progress(s3, "abc12345", "3deDNMr12rQ").update("done", 100, force=True)


if __name__ == "__main__":
    unittest.main()
