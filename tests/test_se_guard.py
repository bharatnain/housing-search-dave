import io, json, tempfile, unittest, urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
import se_api as S
from conftest import FIXTURES


def ok_response():
    return io.BytesIO(json.dumps({"data": {"ok": True}}).encode())


def http_error(code):
    return urllib.error.HTTPError(S.ENDPOINT, code, "err", {}, None)


class Guard(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(S, "STATE", self.tmp / "se_guard.json"),
                        mock.patch.object(S, "MIN_GAP_S", 0), mock.patch.object(S, "JITTER_S", 0),
                        mock.patch.object(S, "_calls", [0])]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in self.patches: p.stop()

    def test_403_starts_cooldown_that_blocks_the_next_run_without_calling(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(403)):
            with self.assertRaises(S.Blocked):
                S.gql("{ ok }")
        state = json.loads(S.STATE.read_text())
        until = datetime.fromisoformat(state["blocked_until"])
        self.assertGreater(until, datetime.now(timezone.utc) + timedelta(hours=S.COOLDOWN_H - 1))
        S._calls[0] = 0   # a fresh process
        with mock.patch("urllib.request.urlopen") as net:
            with self.assertRaises(S.Blocked) as cm:
                S.gql("{ ok }")
            net.assert_not_called()
        self.assertIn("cooling down", str(cm.exception))

    def test_429_counts_as_a_block(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(429)):
            with self.assertRaises(S.Blocked):
                S.gql("{ ok }")
        self.assertIn("blocked_until", json.loads(S.STATE.read_text()))

    def test_daily_budget_is_shared_across_processes(self):
        with mock.patch.object(S, "DAILY_BUDGET", 2), \
             mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: ok_response()):
            S.gql("{ ok }")
            S._calls[0] = 0   # second script, same day
            S.gql("{ ok }")
            S._calls[0] = 0
            with self.assertRaises(S.Blocked) as cm:
                S.gql("{ ok }")
        self.assertIn("daily", str(cm.exception))
        self.assertEqual(json.loads(S.STATE.read_text())["calls"], 2)

    def test_expired_cooldown_and_new_day_reset(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        S.STATE.write_text(json.dumps({"day": "2000-01-01", "calls": 999, "blocked_until": past}))
        with mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: ok_response()):
            self.assertEqual(S.gql("{ ok }"), {"ok": True})
        state = json.loads(S.STATE.read_text())
        self.assertEqual(state["calls"], 1)
        self.assertNotIn("blocked_until", state)


class DetailsPacing(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(S, "STATE", self.tmp / "se_guard.json"),
                        mock.patch.object(S, "_last_call", [0.0])]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in self.patches: p.stop()

    def test_details_tick_waits_at_least_the_details_gap_since_the_last_call(self):
        S.STATE.write_text(json.dumps({"day": datetime.now(timezone.utc).date().isoformat(),
                                       "calls": 0, "last_call_at": 1000.0}))
        slept = []
        with mock.patch.object(S, "DETAILS_GAP_S", 10), mock.patch.object(S, "DETAILS_JITTER_S", 0), \
             mock.patch("time.time", return_value=1002.0), mock.patch("time.sleep", side_effect=slept.append):
            S.guard_tick("details")
        self.assertEqual(slept, [8.0])
        self.assertEqual(json.loads(S.STATE.read_text())["calls"], 1)

    def test_details_reads_use_the_details_spacing(self):
        with mock.patch.object(S, "gql", return_value={"rentalByListingId": None}) as g:
            S.details("1")
        self.assertEqual(g.call_args.kwargs["kind"], "details")


class SharedWithMcp(unittest.TestCase):
    """The MCP runbook ticks and blocks through the CLI; the API path must honor it."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(S, "STATE", self.tmp / "se_guard.json"),
                        mock.patch.object(S, "MIN_GAP_S", 0), mock.patch.object(S, "JITTER_S", 0),
                        mock.patch.object(S, "_calls", [0]), mock.patch.object(S, "DAILY_BUDGET", 120),
                        mock.patch.dict("os.environ", {"DOORMAN_PROFILE": str(FIXTURES / "solo-studio.yaml")})]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in self.patches: p.stop()

    def test_block_recorded_on_the_mcp_path_stops_gql(self):
        self.assertEqual(S.main(["guard-block", "403"]), 0)
        with mock.patch("urllib.request.urlopen") as net:
            with self.assertRaises(S.Blocked):
                S.gql("{ ok }")
            net.assert_not_called()

    def test_guard_tick_counts_a_call(self):
        self.assertEqual(S.main(["guard-tick"]), 0)
        self.assertEqual(json.loads(S.STATE.read_text())["calls"], 1)

    def test_guard_tick_refuses_when_blocked(self):
        S.guard_block(429)
        self.assertEqual(S.main(["guard-tick"]), 2)

    def test_guard_tick_refuses_at_the_profile_cap(self):
        S.STATE.write_text(json.dumps({"day": datetime.now(timezone.utc).date().isoformat(), "calls": 120}))
        self.assertEqual(S.main(["guard-tick", "--kind", "details"]), 2)
        self.assertEqual(json.loads(S.STATE.read_text())["calls"], 120)


if __name__ == "__main__":
    unittest.main()
