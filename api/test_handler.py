"""Unit tests for api/handler.py. Run from the project root: python api/test_handler.py"""
import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

_spec = importlib.util.spec_from_file_location("api_handler", Path(__file__).with_name("handler.py"))
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)

VID = "3deDNMr12rQ"


def item(**kw):
    base = {
        "snippet": {"title": "Slipknot - Three Nil", "categoryId": "10", "liveBroadcastContent": "none"},
        "contentDetails": {"duration": "PT4M49S"},
        "status": {"privacyStatus": "public"},
        "topicDetails": {"topicCategories": []},
    }
    for k, v in kw.items():
        base[k].update(v) if isinstance(v, dict) else base.__setitem__(k, v)
    return base


class ExtractVideoId(unittest.TestCase):
    def test_accepts_common_forms(self):
        good = [
            f"https://www.youtube.com/watch?v={VID}",
            f"https://youtube.com/watch?v={VID}&list=PLabc&t=10s",
            f"http://m.youtube.com/watch?v={VID}",
            f"https://music.youtube.com/watch?v={VID}&si=xyz",
            f"https://youtu.be/{VID}?si=abc",
            f"youtu.be/{VID}",
            f"https://www.youtube.com/shorts/{VID}",
            f"https://www.youtube.com/embed/{VID}",
            f"  https://www.youtube.com/watch?v={VID}  ",
            VID,
        ]
        for url in good:
            self.assertEqual(h.extract_video_id(url), VID, url)

    def test_rejects_everything_else(self):
        bad = [
            None, 123, "", "   ", "not a url", "https://example.com/watch?v=" + VID,
            f"https://evil.com/?u=https://www.youtube.com/watch?v={VID}",
            f"https://www.youtube.com.evil.com/watch?v={VID}",
            f"https://youtube.com@evil.com/watch?v={VID}",
            "https://www.youtube.com/playlist?list=PLabc",
            "https://www.youtube.com/watch?v=short",
            f"https://www.youtube.com/watch?v={VID}; rm -rf /",
            "file:///etc/passwd", "javascript:alert(1)",
            "https://www.youtube.com/watch?v=" + "a" * 500,
            "https://www.youtube.com/@channel",
        ]
        for url in bad:
            self.assertIsNone(h.extract_video_id(url), url)


class ParseDuration(unittest.TestCase):
    def test_values(self):
        self.assertEqual(h.parse_iso_duration("PT4M49S"), 289)
        self.assertEqual(h.parse_iso_duration("PT1H2M3S"), 3723)
        self.assertEqual(h.parse_iso_duration("PT30S"), 30)
        self.assertEqual(h.parse_iso_duration("P1DT1S"), 86401)
        for bad in ("", "garbage", "P", "PT", None):
            self.assertIsNone(h.parse_iso_duration(bad), bad)


class ValidateItem(unittest.TestCase):
    def code(self, it):
        with self.assertRaises(h.ApiError) as cm:
            h.validate_item(it)
        return cm.exception.code

    def test_ok(self):
        self.assertEqual(h.validate_item(item()), "Slipknot - Three Nil")

    def test_music_by_topic_when_category_differs(self):
        it = item(snippet={"categoryId": "24"}, topicDetails={"topicCategories": ["https://en.wikipedia.org/wiki/Music"]})
        h.validate_item(it)

    def test_rejections(self):
        self.assertEqual(self.code(None), "unavailable")
        self.assertEqual(self.code(item(status={"privacyStatus": "private"})), "unavailable")
        self.assertEqual(self.code(item(snippet={"liveBroadcastContent": "live"})), "live")
        self.assertEqual(self.code(item(snippet={"liveBroadcastContent": "upcoming"})), "live")
        self.assertEqual(self.code(item(contentDetails={"duration": "PT19S"})), "too_short")
        self.assertEqual(self.code(item(contentDetails={"duration": "PT8M1S"})), "too_long")
        self.assertEqual(self.code(item(contentDetails={"duration": "P0D"})), "unavailable")
        self.assertEqual(self.code(item(snippet={"categoryId": "20"})), "not_music")

    def test_boundaries_accepted(self):
        h.validate_item(item(contentDetails={"duration": "PT30S"}))
        h.validate_item(item(contentDetails={"duration": "PT8M"}))


def event(body, **kw):
    return {"body": body if isinstance(body, str) else json.dumps(body), **kw}


class ProcessHandler(unittest.TestCase):
    def setUp(self):
        self.clients = {n: mock.Mock() for n in ("s3", "sqs", "ec2", "ssm")}
        self.clients["sqs"].get_queue_attributes.return_value = {
            "Attributes": {"ApproximateNumberOfMessages": "0", "ApproximateNumberOfMessagesNotVisible": "0"}}
        self.clients["ec2"].describe_instances.return_value = {
            "Reservations": [{"Instances": [{"InstanceId": "i-123", "State": {"Name": "stopped"}}]}]}
        self.patches = [
            mock.patch.object(h, "client", side_effect=lambda n: self.clients[n]),
            mock.patch.object(h, "read_json", return_value=None),
            mock.patch.object(h, "fetch_video_item", return_value=item()),
            mock.patch.object(h, "QUEUE_URL", "https://sqs/queue"),
        ]
        self.mocks = [p.start() for p in self.patches]
        self.read_json = self.mocks[1]
        self.fetch = self.mocks[2]

    def tearDown(self):
        mock.patch.stopall()

    def call(self, body):
        r = h.process_handler(event(body), None)
        return r["statusCode"], json.loads(r["body"])

    def test_queues_job_and_starts_instance(self):
        status, body = self.call({"url": f"https://youtu.be/{VID}"})
        self.assertEqual((status, body["status"], body["video_id"]), (202, "queued", VID))
        sent = json.loads(self.clients["sqs"].send_message.call_args.kwargs["MessageBody"])
        self.assertEqual(sent, {"job_id": body["job_id"], "video_id": VID})
        self.clients["ec2"].start_instances.assert_called_once_with(InstanceIds=["i-123"])
        progress = json.loads(self.clients["s3"].put_object.call_args.kwargs["Body"])
        self.assertEqual(progress["status"], "queued")

    def test_does_not_start_running_instance(self):
        self.clients["ec2"].describe_instances.return_value = {
            "Reservations": [{"Instances": [{"InstanceId": "i-123", "State": {"Name": "running"}}]}]}
        self.assertEqual(self.call({"url": VID})[0], 202)
        self.clients["ec2"].start_instances.assert_not_called()

    def test_start_failure_still_queues(self):
        self.clients["ec2"].start_instances.side_effect = RuntimeError("IncorrectInstanceState")
        self.assertEqual(self.call({"url": VID})[0], 202)
        self.clients["sqs"].send_message.assert_called_once()

    def test_cached_stems_skip_the_queue(self):
        self.read_json.return_value = {"name": "Slipknot - Three Nil", "format": "m4a"}
        status, body = self.call({"url": VID})
        self.assertEqual((status, body["status"]), (200, "done"))
        self.clients["sqs"].send_message.assert_not_called()
        self.clients["ec2"].start_instances.assert_not_called()
        progress = json.loads(self.clients["s3"].put_object.call_args.kwargs["Body"])
        self.assertEqual((progress["status"], progress["name"], progress["ext"]), ("done", "Slipknot - Three Nil", "m4a"))

    def test_old_opus_stems_in_s3_are_reprocessed(self):
        for meta in ({"name": "Old"}, {"name": "Old", "format": "opus"}):
            self.read_json.return_value = meta
            self.clients["sqs"].send_message.reset_mock()
            status, body = self.call({"url": VID})
            self.assertEqual((status, body["status"]), (202, "queued"), meta)
            self.clients["sqs"].send_message.assert_called_once()

    def test_bad_input(self):
        for body in ('not json', '[]', '{"url": 5}', '{"url": "https://evil.com/x"}', '{}'):
            status, resp = h.process_handler(event(body), None)["statusCode"], None
            self.assertEqual(status, 400, body)
        self.assertEqual(h.process_handler(event("x" * 5000), None)["statusCode"], 400)
        self.assertEqual(h.process_handler({"body": None}, None)["statusCode"], 400)
        self.clients["sqs"].send_message.assert_not_called()

    def test_rejected_video_never_reaches_the_queue(self):
        self.fetch.return_value = item(contentDetails={"duration": "PT19S"})
        status, body = self.call({"url": VID})
        self.assertEqual((status, body["error"]["code"]), (400, "too_short"))
        self.clients["sqs"].send_message.assert_not_called()

    def test_data_api_outage_fails_open(self):
        self.fetch.side_effect = OSError("down")
        self.assertEqual(self.call({"url": VID})[0], 202)

    def test_busy_queue(self):
        self.clients["sqs"].get_queue_attributes.return_value = {
            "Attributes": {"ApproximateNumberOfMessages": "7", "ApproximateNumberOfMessagesNotVisible": "3"}}
        status, body = self.call({"url": VID})
        self.assertEqual((status, body["error"]["code"]), (429, "busy"))

    def test_kill_switch_denial_gives_a_clear_paused_message(self):
        from types import SimpleNamespace
        denied = RuntimeError("explicit deny in an identity-based policy")
        denied.response = {"Error": {"Code": "AccessDenied"}}
        self.clients["sqs"].send_message.side_effect = denied
        r = h.process_handler(event({"url": VID}), None)
        body = json.loads(r["body"])
        self.assertEqual((r["statusCode"], body["error"]["code"]), (503, "paused"))
        self.assertIn("paused", body["error"]["message"])
        self.clients["ec2"].start_instances.assert_not_called()

    def test_unexpected_error_hides_details(self):
        self.clients["sqs"].send_message.side_effect = RuntimeError("secret internals")
        r = h.process_handler(event({"url": VID}), None)
        self.assertEqual(r["statusCode"], 500)
        self.assertNotIn("secret", r["body"])


class ProgressHandler(unittest.TestCase):
    def setUp(self):
        self.s3 = mock.Mock()
        self.s3.generate_presigned_url.side_effect = lambda op, Params, ExpiresIn: f"https://s3/{Params['Key']}?sig"
        self.ec2 = mock.Mock()
        self.ec2.describe_instances.return_value = {
            "Reservations": [{"Instances": [{"InstanceId": "i-1", "State": {"Name": "stopped"}}]}]}
        self.read = mock.patch.object(h, "read_json")
        self.read_json = self.read.start()
        mock.patch.object(h, "client", side_effect=lambda n: {"s3": self.s3, "ec2": self.ec2}[n]).start()

    def tearDown(self):
        mock.patch.stopall()

    def call(self, job_id):
        r = h.progress_handler({"pathParameters": {"job_id": job_id}}, None)
        return r["statusCode"], json.loads(r["body"])

    def test_done_includes_urls(self):
        self.read_json.return_value = {"job_id": "abc12345", "video_id": VID, "status": "done", "percent": 100,
                                       "ext": "m4a"}
        status, body = self.call("abc12345")
        self.assertEqual(status, 200)
        self.assertEqual(sorted(body["urls"]), h.STEMS)
        self.assertEqual(body["urls"]["guitar"], f"https://s3/stems/{VID}/guitar.m4a?sig")
        self.assertEqual(body["expires_in"], 3600)

    def test_in_progress_has_no_urls(self):
        self.read_json.return_value = {"job_id": "abc12345", "video_id": VID, "status": "separating", "percent": 40}
        _, body = self.call("abc12345")
        self.assertNotIn("urls", body)

    def test_queued_reports_waking_server(self):
        self.read_json.return_value = {"job_id": "abc12345", "video_id": VID, "status": "queued", "message": "Queued"}
        self.assertEqual(self.call("abc12345")[1]["message"], "Waking up the processing server")

    def test_unknown_and_invalid_ids(self):
        self.read_json.return_value = None
        self.assertEqual(self.call("abc12345")[0], 404)
        for bad in ("", "../etc/passwd", "a" * 100, "abc 123", "x"):
            self.assertEqual(self.call(bad)[0], 400, bad)
        self.assertEqual(h.progress_handler({}, None)["statusCode"], 400)


class Reconcile(unittest.TestCase):
    def run_case(self, waiting, state):
        sqs, ec2 = mock.Mock(), mock.Mock()
        sqs.get_queue_attributes.return_value = {
            "Attributes": {"ApproximateNumberOfMessages": str(waiting), "ApproximateNumberOfMessagesNotVisible": "0"}}
        ec2.describe_instances.return_value = {
            "Reservations": [{"Instances": [{"InstanceId": "i-1", "State": {"Name": state}}]}]}
        with mock.patch.object(h, "client", side_effect=lambda n: {"sqs": sqs, "ec2": ec2}[n]):
            h.reconcile_handler({}, None)
        return ec2

    def test_starts_when_jobs_wait_and_instance_stopped(self):
        self.run_case(2, "stopped").start_instances.assert_called_once()

    def test_leaves_running_stopping_or_idle(self):
        self.run_case(2, "running").start_instances.assert_not_called()
        self.run_case(2, "stopping").start_instances.assert_not_called()
        self.run_case(0, "stopped").start_instances.assert_not_called()


if __name__ == "__main__":
    unittest.main()
