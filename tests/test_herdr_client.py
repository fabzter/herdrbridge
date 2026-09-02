import io, json, os, shutil, socket, sys, threading, tempfile, time, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import herdrbridge as hb


class Completed:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class CliTests(unittest.TestCase):
    def make(self, rc, out="", err=""):
        calls = []
        def runner(argv, **kw):
            calls.append((argv, kw))
            return Completed(rc, out, err)
        return hb.Herdr("bridge-test-1", runner=runner), calls

    def test_cli_parses_json_and_sets_session_env(self):
        h, calls = self.make(0, json.dumps({"id": "x", "result": {"type": "pong"}}))
        res = h.cli("workspace", "list")
        self.assertEqual(res["result"]["type"], "pong")
        argv, kw = calls[0]
        self.assertEqual(argv[:3], ["herdr", "workspace", "list"])
        self.assertEqual(kw["env"]["HERDR_SESSION"], "bridge-test-1")
        self.assertTrue(kw["capture_output"] and kw["text"])

    def test_cli_error_json_on_stderr_raises_herdr_error(self):
        h, _ = self.make(1, "", json.dumps({"id": "x", "error": {"code": "pane_not_found", "message": "pane w1:p9 not found"}}))
        with self.assertRaises(hb.HerdrError) as cm:
            h.cli("pane", "get", "w1:p9")
        self.assertEqual(cm.exception.herdr_code, "pane_not_found")
        self.assertEqual(cm.exception.code, 2)

    def test_cli_non_object_json_on_stderr_raises_generic_herdr_error(self):
        h, _ = self.make(1, "", "[1]")
        with self.assertRaises(hb.HerdrError) as cm:
            h.cli("pane", "get", "w1:p9")
        self.assertEqual(cm.exception.herdr_code, "error")

    def test_cli_non_dict_error_field_on_stderr_raises_generic_herdr_error(self):
        h, _ = self.make(1, "", json.dumps({"error": "boom"}))
        with self.assertRaises(hb.HerdrError) as cm:
            h.cli("pane", "get", "w1:p9")
        self.assertEqual(cm.exception.herdr_code, "error")

    def test_cli_usage_error_exit_2_maps_to_usage_code(self):
        h, _ = self.make(2, "", "error: unexpected argument")
        with self.assertRaises(hb.HerdrError) as cm:
            h.cli("agent", "bogus")
        self.assertEqual(cm.exception.herdr_code, "usage")
        self.assertEqual(cm.exception.code, 1)

    def test_cli_usage_error_message_always_ends_with_joined_argv(self):
        h, _ = self.make(2, "", "error: unexpected argument")
        with self.assertRaises(hb.HerdrError) as cm:
            h.cli("agent", "bogus")
        self.assertTrue(str(cm.exception).endswith("herdr agent bogus"))

    def test_cli_usage_error_with_empty_stderr_message_ends_with_joined_argv(self):
        h, _ = self.make(2, "", "")
        with self.assertRaises(hb.HerdrError) as cm:
            h.cli("agent", "bogus")
        self.assertTrue(str(cm.exception).endswith("herdr agent bogus"))

    def test_cli_text_returns_stdout_verbatim(self):
        h, _ = self.make(0, "line1\nline2\n")
        self.assertEqual(h.cli_text("agent", "read", "bean"), "line1\nline2\n")

    def test_cli_defaults_timeout_to_30s_when_not_given(self):
        h, calls = self.make(0, json.dumps({"id": "x", "result": {}}))
        h.cli("agent", "list")
        _, kw = calls[0]
        self.assertEqual(kw["timeout"], 30)

    def test_cli_passes_through_caller_supplied_timeout(self):
        h, calls = self.make(0, json.dumps({"id": "x", "result": {}}))
        h.cli("agent", "list", timeout_s=5)
        _, kw = calls[0]
        self.assertEqual(kw["timeout"], 5)

    def test_cli_timeout_with_non_string_args_raises_herdr_error(self):
        import subprocess
        def runner(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 5)
        h = hb.Herdr("bridge-test-1", runner=runner)
        with self.assertRaises(hb.HerdrError) as cm:
            h.cli(123, "read", timeout_s=5)
        self.assertEqual(cm.exception.herdr_code, "timeout")
        self.assertEqual(cm.exception.code, 6)

    def test_socket_path_for_named_and_default_session(self):
        home = os.path.expanduser("~")
        self.assertEqual(hb.Herdr("agents").socket_path, os.path.join(home, ".config", "herdr", "sessions", "agents", "herdr.sock"))
        self.assertEqual(hb.Herdr("default").socket_path, os.path.join(home, ".config", "herdr", "herdr.sock"))


class FakeSocketServer(threading.Thread):
    """Answers one connection: replies to each request line via `handler`."""
    def __init__(self, path, handler):
        super().__init__(daemon=True)
        self.path, self.handler = path, handler
        self.srv = socket.socket(socket.AF_UNIX); self.srv.bind(path); self.srv.listen(1)
    def run(self):
        conn, _ = self.srv.accept()
        f = conn.makefile("rwb")
        for line in f:
            for out in self.handler(json.loads(line)):
                f.write((json.dumps(out) + "\n").encode()); f.flush()
        conn.close()


class SocketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, "s.sock")

    def test_request_returns_result(self):
        def handler(req):
            self.assertEqual(req["method"], "ping"); yield {"id": req["id"], "result": {"type": "pong", "version": "0.8.2"}}
        srv = FakeSocketServer(self.path, handler); self.addCleanup(srv.srv.close); srv.start()
        h = hb.Herdr("t", socket_path=self.path)
        self.assertEqual(h.ping()["version"], "0.8.2")

    def test_request_missing_socket_raises_server_unavailable(self):
        h = hb.Herdr("t", socket_path=os.path.join(self.tmp, "missing.sock"))
        with self.assertRaises(hb.ServerUnavailable):
            h.request("ping", {})

    def test_request_connection_refused_raises_server_unavailable(self):
        # Bind-and-close a socket path: connecting to it afterwards raises ConnectionRefusedError.
        path = os.path.join(self.tmp, "refused.sock")
        srv = socket.socket(socket.AF_UNIX); srv.bind(path); srv.listen(1); srv.close()
        h = hb.Herdr("t", socket_path=path)
        with self.assertRaises(hb.ServerUnavailable):
            h.request("ping", {})

    def test_request_error_raises(self):
        def handler(req):
            yield {"id": req["id"], "error": {"code": "not_found", "message": "nope"}}
        srv = FakeSocketServer(self.path, handler); self.addCleanup(srv.srv.close); srv.start()
        with self.assertRaises(hb.HerdrError):
            hb.Herdr("t", socket_path=self.path).request("pane.get", {"pane_id": "w1:p1"})

    def test_request_socket_timeout_raises_herdr_timeout_error(self):
        def handler(req):
            time.sleep(1)
            return
            yield  # pragma: no cover - makes this a generator; never reached
        srv = FakeSocketServer(self.path, handler); self.addCleanup(srv.srv.close); srv.start()
        with self.assertRaises(hb.HerdrError) as cm:
            hb.Herdr("t", socket_path=self.path).request("ping", {}, timeout_s=0.05)
        self.assertEqual(cm.exception.herdr_code, "timeout")

    def test_subscribe_yields_events_after_ack(self):
        def handler(req):
            yield {"id": req["id"], "result": {"type": "subscribed"}}
            yield {"event": "pane.agent_status_changed", "data": {"pane_id": "w1:p1", "workspace_id": "w1", "agent_status": "blocked"}}
        srv = FakeSocketServer(self.path, handler); self.addCleanup(srv.srv.close); srv.start()
        gen = hb.Herdr("t", socket_path=self.path).subscribe([{"type": "pane.agent_status_changed", "pane_id": "w1:p1"}])
        ev = next(gen)
        self.assertEqual(ev["data"]["agent_status"], "blocked")

    def test_subscribe_stops_on_truncated_line_instead_of_raising(self):
        def handler(req):
            yield {"id": req["id"], "result": {"type": "subscribed"}}
            yield {"event": "pane.agent_status_changed", "data": {"pane_id": "w1:p1"}}
        class TruncatingServer(FakeSocketServer):
            def run(self):
                conn, _ = self.srv.accept()
                f = conn.makefile("rwb")
                for line in f:
                    for out in self.handler(json.loads(line)):
                        f.write((json.dumps(out) + "\n").encode()); f.flush()
                    f.write(b'{"event": "trunc"'); f.flush()  # no trailing newline/close: truncated
                    break
                conn.close()
        srv = TruncatingServer(self.path, handler); self.addCleanup(srv.srv.close); srv.start()
        gen = hb.Herdr("t", socket_path=self.path).subscribe([{"type": "pane.agent_status_changed"}])
        events = list(gen)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["data"]["pane_id"], "w1:p1")

    def test_ensure_server_spawns_when_ping_fails(self):
        spawned = []
        pings = {"n": 0}
        h = hb.Herdr("t", socket_path=os.path.join(self.tmp, "missing.sock"), spawner=lambda *a, **k: spawned.append((a, k)))
        def fake_ping():
            pings["n"] += 1
            if pings["n"] < 3: raise OSError("no socket")
            return {"type": "pong"}
        h.ping = fake_ping
        h.ensure_server(wait_s=2, poll_s=0.01)
        self.assertEqual(len(spawned), 1)
        argv = spawned[0][0][0]
        self.assertEqual(argv, ["herdr", "server"])
        self.assertEqual(spawned[0][1]["env"]["HERDR_SESSION"], "t")
        self.assertTrue(spawned[0][1]["start_new_session"])

    def test_ensure_server_raises_after_wait(self):
        h = hb.Herdr("t", socket_path=os.path.join(self.tmp, "missing.sock"), spawner=lambda *a, **k: None)
        h.ping = lambda: (_ for _ in ()).throw(OSError("no"))
        with self.assertRaises(hb.ServerUnavailable):
            h.ensure_server(wait_s=0.05, poll_s=0.01)

    def test_ensure_server_pre_check_catches_server_unavailable_and_still_spawns(self):
        # Regression test: `ping()` -> `request()` now raises `ServerUnavailable` (not `OSError`) when
        # the socket file is simply missing. `ensure_server`'s pre-check must catch that too, or it
        # never reaches the spawn step. Uses the real `ping`/`request` path (no stubbing) so a
        # pre-check `except` clause that omits `ServerUnavailable` is actually exercised.
        spawned = []
        h = hb.Herdr("t", socket_path=os.path.join(self.tmp, "missing.sock"),
                     spawner=lambda *a, **k: spawned.append((a, k)))
        with self.assertRaises(hb.ServerUnavailable):
            h.ensure_server(wait_s=0.05, poll_s=0.01)
        self.assertEqual(len(spawned), 1)


if __name__ == "__main__":
    unittest.main()
