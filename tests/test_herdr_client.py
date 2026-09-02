import io, json, os, socket, sys, threading, tempfile, unittest
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

    def test_cli_usage_error_exit_2_maps_to_usage_code(self):
        h, _ = self.make(2, "", "error: unexpected argument")
        with self.assertRaises(hb.HerdrError) as cm:
            h.cli("agent", "bogus")
        self.assertEqual(cm.exception.herdr_code, "usage")
        self.assertEqual(cm.exception.code, 1)

    def test_cli_text_returns_stdout_verbatim(self):
        h, _ = self.make(0, "line1\nline2\n")
        self.assertEqual(h.cli_text("agent", "read", "bean"), "line1\nline2\n")

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
        self.tmp = tempfile.mkdtemp(); self.path = os.path.join(self.tmp, "s.sock")

    def test_request_returns_result(self):
        def handler(req):
            self.assertEqual(req["method"], "ping"); yield {"id": req["id"], "result": {"type": "pong", "version": "0.8.2"}}
        srv = FakeSocketServer(self.path, handler); self.addCleanup(srv.srv.close); srv.start()
        h = hb.Herdr("t", socket_path=self.path)
        self.assertEqual(h.ping()["version"], "0.8.2")

    def test_request_error_raises(self):
        def handler(req):
            yield {"id": req["id"], "error": {"code": "not_found", "message": "nope"}}
        srv = FakeSocketServer(self.path, handler); self.addCleanup(srv.srv.close); srv.start()
        with self.assertRaises(hb.HerdrError):
            hb.Herdr("t", socket_path=self.path).request("pane.get", {"pane_id": "w1:p1"})

    def test_subscribe_yields_events_after_ack(self):
        def handler(req):
            yield {"id": req["id"], "result": {"type": "subscribed"}}
            yield {"event": "pane.agent_status_changed", "data": {"pane_id": "w1:p1", "workspace_id": "w1", "agent_status": "blocked"}}
        srv = FakeSocketServer(self.path, handler); self.addCleanup(srv.srv.close); srv.start()
        gen = hb.Herdr("t", socket_path=self.path).subscribe([{"type": "pane.agent_status_changed", "pane_id": "w1:p1"}])
        ev = next(gen)
        self.assertEqual(ev["data"]["agent_status"], "blocked")

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


if __name__ == "__main__":
    unittest.main()
