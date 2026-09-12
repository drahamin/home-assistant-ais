import json
import threading
import time
import unittest
from unittest.mock import patch

from state_publisher import StatePublisher


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class StatePublisherTests(unittest.TestCase):
    def test_duplicate_and_pending_values_are_coalesced(self):
        publisher = StatePublisher("http://example/states", "token", lambda _message: None)
        payload = {"state": 51.2, "attributes": {"unit": "V"}}
        publisher.queue("sensor.voltage", payload)
        publisher.queue("sensor.voltage", payload)
        self.assertEqual(publisher.pending_count, 1)
        publisher.queue("sensor.voltage", {"state": 51.3, "attributes": {"unit": "V"}})
        self.assertEqual(publisher.pending_count, 1)

    def test_unchanged_state_is_periodically_refreshed(self):
        publisher = StatePublisher("http://example/states", "token", lambda _message: None, refresh_interval=60)
        payload = {"state": 28, "attributes": {"unit": "%"}}
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        publisher._last_sent["sensor.soc"] = encoded
        publisher._last_sent_at["sensor.soc"] = 10
        with patch("time.monotonic", return_value=69):
            publisher.queue("sensor.soc", payload)
        self.assertEqual(publisher.pending_count, 0)
        with patch("time.monotonic", return_value=70):
            publisher.queue("sensor.soc", payload)
        self.assertEqual(publisher.pending_count, 1)

    def test_worker_publishes_latest_payload(self):
        sent = []
        event = threading.Event()

        def urlopen(request, timeout):
            sent.append((request.full_url, json.loads(request.data), timeout))
            event.set()
            return _Response()

        publisher = StatePublisher("http://example/states", "token", lambda _message: None)
        with patch("urllib.request.urlopen", side_effect=urlopen):
            publisher.queue("sensor.voltage", {"state": 51.2})
            publisher.start()
            self.assertTrue(event.wait(1))
            publisher.stop()
        self.assertEqual(sent[0][0], "http://example/states/sensor.voltage")
        self.assertEqual(sent[0][1]["state"], 51.2)

    def test_queue_is_non_blocking_when_api_is_slow(self):
        release = threading.Event()
        entered = threading.Event()

        def urlopen(_request, timeout):
            entered.set()
            release.wait(1)
            return _Response()

        publisher = StatePublisher("http://example/states", "token", lambda _message: None)
        with patch("urllib.request.urlopen", side_effect=urlopen):
            publisher.start()
            publisher.queue("sensor.one", {"state": 1})
            self.assertTrue(entered.wait(1))
            started = time.monotonic()
            for value in range(1000):
                publisher.queue("sensor.two", {"state": value})
            elapsed = time.monotonic() - started
            release.set()
            publisher.stop()
        self.assertLess(elapsed, 0.1)
        self.assertLessEqual(publisher.pending_count, 1)
