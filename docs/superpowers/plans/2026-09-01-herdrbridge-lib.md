# herdrbridge library — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `herdrbridge.py`, the stdlib-only Python library that both agent bridges (`fabzter/hermes-bridge`, Claude Code → Hermes; `fabzter/hermes-claude-bridge`, Hermes → Claude Code) vendor: herdr client (CLI + socket), state store, state classification, reply extraction, approval-menu planner, and the `Bridge` operations.

**Architecture:** One module `herdrbridge.py` at the repo root with a `unittest` suite that injects a scripted `FakeHerdr` (`tests/fakes.py`) and pane-transcript fixtures (`tests/fixtures/`). Downstream repos copy `herdrbridge.py`, `tests/fakes.py` and the fixtures at a pinned commit through their own `tools/sync-lib.sh`; nothing here is ever installed as a package.

**Tech Stack:** Python 3.9+ stdlib only (`subprocess`, `socket`, `json`, `dataclasses`, `unittest`), herdr 0.8.2 CLI + socket API.

**Spec:** `docs/superpowers/specs/2026-09-01-herdr-bridges-design.md` (copy of the canonical spec in fabzter/hermes-bridge). Section numbers below refer to it.

## Global Constraints

- Repo root is `/Users/fabzter/src/herdrbridge` (GitHub `fabzter/herdrbridge`, public so downstream can fetch raw files). Layout: `herdrbridge.py` at the root, `tests/`, `tests/fakes.py`, `tests/fixtures/`.
- Python 3 stdlib only; Python 3.9 compatible (`from __future__ import annotations`, no `match`, no `X | Y` outside annotations).
- Public API stability: every name listed under a task's **Interfaces → Produces** is consumed by the two downstream plans. Do not rename or change signatures without noting it in this plan and in both downstream plans.
- herdr session handling: `session_name()` returns `HERDR_BRIDGE_SESSION` or `agents`; the library never touches the default herdr session and never stops servers.
- Tests: `python3 -m unittest discover -s tests -v` from the repo root passes before every commit. Also run with `/Users/fabzter/.hermes/hermes-agent/venv/bin/python` in Task 7.
- Commit messages carry no attribution footers of any kind. Push after each task (`git push origin main`; the repo has a repo-local credential helper for the `fabzter` account).
- No AI authorship attribution anywhere: commit messages, code comments, docstrings, READMEs and SKILL.md must not say the code was written or co-authored by an AI tool (Claude Code may be *mentioned as the agent the bridge talks to*, never as the author). Remove such sentences from existing docs you rewrite.

## File structure

| File | Responsibility |
|---|---|
| `herdrbridge.py` | The library: constants, errors, `Herdr` client, `StateStore`, classification, reply extraction, menu planner, topology + resolve, `Bridge` operations |
| `tests/fakes.py` | `FakeHerdr` scripted client and helpers, vendored downstream for their tests |
| `tests/fixtures/*.txt` | Pane transcripts (Hermes REPL, Claude alt-screen, approval menu) |
| `tests/test_*.py` | One module per library area |
| `README.md` | What it is, how downstream vendors it (Task 7 adds it) |

---

### Task 1: Test scaffolding, constants, errors, name validation

**Files:**
- Create: `herdrbridge.py`
- Create: `tests/__init__.py` (empty)
- Create: `tests/test_names.py`

**Interfaces:**
- Produces: `validate_name(name: str) -> str`, exit-code constants, `BridgeError(code:int, message:str)`, `UsageError`, `ServerUnavailable`, `HerdrError(herdr_code:str, message:str)`, `herdr_error_exit(herdr_code) -> int`, `SESSION_NAME` / `session_name()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_names.py
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import herdrbridge as hb


class NameTests(unittest.TestCase):
    def test_valid_names(self):
        for n in ["a", "bean", "hermes-cv", "cv_2", "x" * 32]:
            self.assertEqual(hb.validate_name(n), n)

    def test_invalid_names_raise_usage_error(self):
        for n in ["", "Bean", "1abc", "hermes.cv", "a/b", "x" * 33, "with space"]:
            with self.assertRaises(hb.UsageError) as cm:
                hb.validate_name(n)
            self.assertEqual(cm.exception.code, 2)


class ErrorTests(unittest.TestCase):
    def test_exit_code_constants(self):
        self.assertEqual((hb.EXIT_OK, hb.EXIT_ERROR, hb.EXIT_MISSING, hb.EXIT_APPROVAL,
                          hb.EXIT_SECRET, hb.EXIT_CLARIFY, hb.EXIT_TIMEOUT, hb.EXIT_DEAD,
                          hb.EXIT_BUSY, hb.EXIT_SERVER), (0, 1, 2, 3, 4, 5, 6, 7, 8, 9))

    def test_herdr_error_mapping(self):
        self.assertEqual(hb.herdr_error_exit("timeout"), 6)
        self.assertEqual(hb.herdr_error_exit("pane_not_found"), 2)
        self.assertEqual(hb.herdr_error_exit("not_found"), 2)
        self.assertEqual(hb.herdr_error_exit("agent_not_found"), 2)
        self.assertEqual(hb.herdr_error_exit("agent_not_running"), 7)
        self.assertEqual(hb.herdr_error_exit("agent_blocked"), 3)
        self.assertEqual(hb.herdr_error_exit("something_else"), 1)
        e = hb.HerdrError("timeout", "wait timed out")
        self.assertEqual(e.code, 6)
        self.assertIn("timeout", str(e))

    def test_session_name_env_override(self):
        old = os.environ.get("HERDR_BRIDGE_SESSION")
        try:
            os.environ["HERDR_BRIDGE_SESSION"] = "bridge-test-1"
            self.assertEqual(hb.session_name(), "bridge-test-1")
            del os.environ["HERDR_BRIDGE_SESSION"]
            self.assertEqual(hb.session_name(), "agents")
        finally:
            if old is not None:
                os.environ["HERDR_BRIDGE_SESSION"] = old


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/fabzter/src/herdrbridge && python3 -m unittest tests.test_names -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'herdrbridge'`.

- [ ] **Step 3: Write the library skeleton**

```python
# herdrbridge.py
"""herdrbridge — shared plumbing for the Claude<->Hermes bridges on herdr.

Canonical repo: fabzter/herdrbridge. fabzter/hermes-bridge and
fabzter/hermes-claude-bridge vendor pinned copies via tools/sync-lib.sh; change here first.
Stdlib only; Python 3.9+.
"""
from __future__ import annotations

import os
import re

SESSION_DEFAULT = "agents"
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

EXIT_OK, EXIT_ERROR, EXIT_MISSING, EXIT_APPROVAL, EXIT_SECRET = 0, 1, 2, 3, 4
EXIT_CLARIFY, EXIT_TIMEOUT, EXIT_DEAD, EXIT_BUSY, EXIT_SERVER = 5, 6, 7, 8, 9

_HERDR_ERROR_EXITS = {
    "timeout": EXIT_TIMEOUT,
    "pane_not_found": EXIT_MISSING,
    "not_found": EXIT_MISSING,
    "agent_not_found": EXIT_MISSING,
    "agent_not_running": EXIT_DEAD,
    "agent_blocked": EXIT_APPROVAL,
}


def session_name() -> str:
    return os.environ.get("HERDR_BRIDGE_SESSION") or SESSION_DEFAULT


class BridgeError(Exception):
    """Base error; `code` is the process exit code."""
    code = EXIT_ERROR

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        if code is not None:
            self.code = code


class UsageError(BridgeError):
    code = 2


class ServerUnavailable(BridgeError):
    code = EXIT_SERVER


class HerdrError(BridgeError):
    """A JSON error returned by the herdr CLI or socket."""

    def __init__(self, herdr_code: str, message: str):
        self.herdr_code = herdr_code
        super().__init__("herdr %s: %s" % (herdr_code, message), herdr_error_exit(herdr_code))


def herdr_error_exit(herdr_code: str) -> int:
    return _HERDR_ERROR_EXITS.get(herdr_code, EXIT_ERROR)


def validate_name(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise UsageError(
            "invalid session name %r: must match [a-z][a-z0-9_-]{0,31} "
            "(lowercase letters, digits, '_' and '-', max 32 chars, letter first)" % (name,))
    return name
```

Also create empty `tests/__init__.py`.

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m unittest tests.test_names -v`
Expected: 4 tests, OK.

- [ ] **Step 5: Commit**

```bash
git add herdrbridge.py tests/__init__.py tests/test_names.py
git commit -m "herdrbridge: constants, errors, name validation"
```

---

### Task 2: `Herdr` client — CLI runner, socket request, ping, ensure_server

**Files:**
- Modify: `herdrbridge.py`
- Create: `tests/test_herdr_client.py`

**Interfaces:**
- Produces:
  - `class Herdr(session: str, bin: str = "herdr", runner=subprocess.run, spawner=subprocess.Popen, connector=None)`
  - `Herdr.socket_path -> str`; `Herdr.env() -> dict`
  - `Herdr.cli(*args, timeout_s: float | None = None) -> dict` (parsed JSON `result`-bearing response; raises `HerdrError`)
  - `Herdr.cli_text(*args, timeout_s=None) -> str` (raw stdout for `agent read` / `pane read`)
  - `Herdr.request(method: str, params: dict, timeout_s: float = 30) -> dict` (raw socket; returns the `result` dict)
  - `Herdr.ping() -> dict`; `Herdr.ensure_server(wait_s: float = 10) -> None`
  - `Herdr.subscribe(subscriptions: list[dict])` generator yielding event envelopes `{"event":..., "data":...}`
  - `Herdr.wait_event(match_event: dict, timeout_ms: int) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_herdr_client.py
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
        FakeSocketServer(self.path, handler).start()
        h = hb.Herdr("t", socket_path=self.path)
        self.assertEqual(h.ping()["version"], "0.8.2")

    def test_request_error_raises(self):
        def handler(req):
            yield {"id": req["id"], "error": {"code": "not_found", "message": "nope"}}
        FakeSocketServer(self.path, handler).start()
        with self.assertRaises(hb.HerdrError):
            hb.Herdr("t", socket_path=self.path).request("pane.get", {"pane_id": "w1:p1"})

    def test_subscribe_yields_events_after_ack(self):
        def handler(req):
            yield {"id": req["id"], "result": {"type": "subscribed"}}
            yield {"event": "pane.agent_status_changed", "data": {"pane_id": "w1:p1", "workspace_id": "w1", "agent_status": "blocked"}}
        FakeSocketServer(self.path, handler).start()
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
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_herdr_client -v`
Expected: ERROR `AttributeError: module 'herdrbridge' has no attribute 'Herdr'`.

- [ ] **Step 3: Implement the client**

Append to `herdrbridge.py` (add `import json, socket, subprocess, time, uuid` at top):

```python
class Herdr:
    """Thin client for one named herdr session: CLI wrappers + raw socket."""

    def __init__(self, session: str, bin: str = "herdr", runner=None, spawner=None, socket_path: str | None = None):
        self.session = session
        self.bin = bin
        self._runner = runner or subprocess.run
        self._spawner = spawner or subprocess.Popen
        self._socket_path = socket_path

    # --- environment -----------------------------------------------------
    @property
    def config_dir(self) -> str:
        return os.path.join(os.path.expanduser("~"), ".config", "herdr")

    @property
    def session_dir(self) -> str:
        if self.session == "default":
            return self.config_dir
        return os.path.join(self.config_dir, "sessions", self.session)

    @property
    def socket_path(self) -> str:
        return self._socket_path or os.path.join(self.session_dir, "herdr.sock")

    def env(self) -> dict:
        env = dict(os.environ)
        env["HERDR_SESSION"] = self.session
        env.pop("HERDR_SOCKET_PATH", None)
        return env

    # --- CLI -------------------------------------------------------------
    def _run(self, args, timeout_s):
        argv = [self.bin] + [str(a) for a in args]
        try:
            cp = self._runner(argv, env=self.env(), capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            raise HerdrError("timeout", "herdr %s exceeded %ss" % (" ".join(args[:2]), timeout_s))
        if cp.returncode == 0:
            return cp
        if cp.returncode == 2:
            raise HerdrError("usage", (cp.stderr or "").strip() or "herdr usage error: %s" % " ".join(argv))
        try:
            err = json.loads((cp.stderr or "").strip().splitlines()[-1])["error"]
            raise HerdrError(str(err.get("code", "error")), str(err.get("message", "")))
        except (ValueError, KeyError, IndexError):
            raise HerdrError("error", (cp.stderr or cp.stdout or "").strip() or "herdr exited %d" % cp.returncode)

    def cli(self, *args, timeout_s: float | None = None) -> dict:
        cp = self._run(args, timeout_s)
        try:
            return json.loads(cp.stdout)
        except ValueError:
            raise HerdrError("bad_json", "non-JSON output from herdr %s: %r" % (" ".join(map(str, args[:2])), cp.stdout[:200]))

    def cli_text(self, *args, timeout_s: float | None = None) -> str:
        return self._run(args, timeout_s).stdout

    # --- raw socket ------------------------------------------------------
    def _connect(self, timeout_s):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout_s)
        s.connect(self.socket_path)
        return s

    @staticmethod
    def _parse_response(line: bytes) -> dict:
        msg = json.loads(line.decode("utf-8"))
        if "error" in msg:
            err = msg["error"] or {}
            raise HerdrError(str(err.get("code", "error")), str(err.get("message", "")))
        return msg

    def request(self, method: str, params: dict, timeout_s: float = 30) -> dict:
        s = self._connect(timeout_s)
        try:
            rid = "bridge-%s" % uuid.uuid4().hex[:8]
            s.sendall((json.dumps({"id": rid, "method": method, "params": params}) + "\n").encode("utf-8"))
            f = s.makefile("rb")
            line = f.readline()
            if not line:
                raise HerdrError("closed", "herdr closed the socket without answering %s" % method)
            return self._parse_response(line).get("result", {})
        finally:
            s.close()

    def ping(self) -> dict:
        return self.request("ping", {}, timeout_s=3)

    def wait_event(self, match_event: dict, timeout_ms: int) -> dict:
        return self.request("events.wait", {"match_event": match_event, "timeout_ms": timeout_ms},
                            timeout_s=timeout_ms / 1000.0 + 5)

    def subscribe(self, subscriptions: list):
        """Yield event envelopes forever (until the socket closes)."""
        s = self._connect(None)
        try:
            s.sendall((json.dumps({"id": "bridge-sub", "method": "events.subscribe",
                                   "params": {"subscriptions": subscriptions}}) + "\n").encode("utf-8"))
            f = s.makefile("rb")
            ack = f.readline()
            if not ack:
                raise HerdrError("closed", "no subscribe ack")
            self._parse_response(ack)
            for line in f:
                if line.strip():
                    yield json.loads(line.decode("utf-8"))
        finally:
            s.close()

    # --- server lifecycle -------------------------------------------------
    def ensure_server(self, wait_s: float = 10, poll_s: float = 0.5) -> None:
        try:
            self.ping()
            return
        except (OSError, HerdrError, ValueError):
            pass
        os.makedirs(self.session_dir, exist_ok=True)
        log_path = os.path.join(self.session_dir, "herdr-server.log")
        log = open(log_path, "ab")
        self._spawner([self.bin, "server"], env=self.env(), stdin=subprocess.DEVNULL,
                      stdout=log, stderr=log, start_new_session=True)
        deadline = time.time() + wait_s
        while time.time() < deadline:
            try:
                self.ping()
                return
            except (OSError, HerdrError, ValueError):
                time.sleep(poll_s)
        raise ServerUnavailable("herdr server for session %r did not answer within %ss (log: %s)"
                                % (self.session, wait_s, log_path))
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m unittest tests.test_herdr_client -v`
Expected: 10 tests OK.

- [ ] **Step 5: Commit**

```bash
git add herdrbridge.py tests/test_herdr_client.py
git commit -m "herdrbridge: Herdr client (CLI runner, socket request/subscribe, ensure_server)"
```

---

### Task 3: `StateStore` with legacy `.session-id` migration

**Files:**
- Modify: `herdrbridge.py`
- Create: `tests/test_state_store.py`

**Interfaces:**
- Produces: `class StateStore(dir: str)` with `load(name) -> dict`, `save(name, **fields) -> dict` (merges, sets `updated_at`), `delete(name) -> bool`, `names() -> list[str]`. Fields used by callers: `agent_session_id`, `pane_id`, `tab_id`, `cwd`, `launch_flags` (list), `updated_at`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_state_store.py
import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import herdrbridge as hb


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(); self.store = hb.StateStore(self.dir)

    def test_load_missing_is_empty(self):
        self.assertEqual(self.store.load("bean"), {})

    def test_save_merges_and_round_trips(self):
        self.store.save("bean", agent_session_id="20260901_1", pane_id="w1:p1")
        self.store.save("bean", tab_id="w1:t2")
        d = self.store.load("bean")
        self.assertEqual(d["agent_session_id"], "20260901_1")
        self.assertEqual(d["pane_id"], "w1:p1"); self.assertEqual(d["tab_id"], "w1:t2")
        self.assertIn("updated_at", d)
        with open(os.path.join(self.dir, "bean.json")) as f:
            self.assertEqual(json.load(f)["pane_id"], "w1:p1")

    def test_save_none_removes_key(self):
        self.store.save("bean", pane_id="w1:p1"); self.store.save("bean", pane_id=None)
        self.assertNotIn("pane_id", self.store.load("bean"))

    def test_legacy_session_id_file_migrates_once(self):
        with open(os.path.join(self.dir, "hermes-cv.session-id"), "w") as f:
            f.write("20260827_113000_abcdef")
        # legacy name has a '-' only, so it is a valid new name too
        d = self.store.load("hermes-cv")
        self.assertEqual(d["agent_session_id"], "20260827_113000_abcdef")
        self.assertTrue(os.path.exists(os.path.join(self.dir, "hermes-cv.json")))

    def test_delete_and_names(self):
        self.store.save("a", x=1); self.store.save("b", x=2)
        self.assertEqual(self.store.names(), ["a", "b"])
        self.assertTrue(self.store.delete("a")); self.assertFalse(self.store.delete("a"))
        self.assertEqual(self.store.names(), ["b"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_state_store -v` → `AttributeError: ... 'StateStore'`.

- [ ] **Step 3: Implement**

Append to `herdrbridge.py` (add `import datetime`):

```python
class StateStore:
    """One JSON file per session name under `dir`; migrates old `<name>.session-id` files."""

    def __init__(self, dir: str):
        self.dir = dir

    def _path(self, name: str) -> str:
        return os.path.join(self.dir, "%s.json" % name)

    def load(self, name: str) -> dict:
        p = self._path(name)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                try:
                    return json.load(f)
                except ValueError:
                    return {}
        legacy = os.path.join(self.dir, "%s.session-id" % name)
        if os.path.exists(legacy):
            with open(legacy, "r", encoding="utf-8") as f:
                sid = f.read().strip()
            if sid:
                return self.save(name, agent_session_id=sid, migrated_from="session-id")
        return {}

    def save(self, name: str, **fields) -> dict:
        os.makedirs(self.dir, exist_ok=True)
        data = self.load(name) if os.path.exists(self._path(name)) else {}
        for k, v in fields.items():
            if v is None:
                data.pop(k, None)
            else:
                data[k] = v
        data["updated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        tmp = self._path(name) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        os.replace(tmp, self._path(name))
        return data

    def delete(self, name: str) -> bool:
        p = self._path(name)
        if os.path.exists(p):
            os.remove(p)
            return True
        return False

    def names(self) -> list:
        if not os.path.isdir(self.dir):
            return []
        return sorted(f[:-5] for f in os.listdir(self.dir) if f.endswith(".json"))
```

- [ ] **Step 4: Run to verify pass** → 5 tests OK.

- [ ] **Step 5: Commit**

```bash
git add herdrbridge.py tests/test_state_store.py
git commit -m "herdrbridge: StateStore with legacy session-id migration"
```

---

### Task 4: State classification

**Files:**
- Modify: `herdrbridge.py`
- Create: `tests/test_classify.py`

**Interfaces:**
- Produces: `STATES` tuple, `RULE_STATES: dict[str,str]`, `STATE_EXIT: dict[str,int]`, `classify(agent_status: str | None, matched_rule_id: str | None) -> str`, `state_exit(state: str) -> int`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_classify.py
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import herdrbridge as hb


class ClassifyTests(unittest.TestCase):
    def test_idle_and_done_are_idle(self):
        self.assertEqual(hb.classify("idle", None), "idle")
        self.assertEqual(hb.classify("done", None), "idle")

    def test_working_is_busy(self):
        self.assertEqual(hb.classify("working", None), "busy")

    def test_blocked_rules_hermes(self):
        self.assertEqual(hb.classify("blocked", "dangerous_command_approval"), "approval")
        self.assertEqual(hb.classify("blocked", "confirmation_prompt"), "approval")
        self.assertEqual(hb.classify("blocked", "credential_prompt"), "secret")
        self.assertEqual(hb.classify("blocked", "clarification_prompt"), "clarify")

    def test_blocked_rules_claude(self):
        for r in ("bash_permission_prompt", "generic_permission_prompt", "legacy_no_prompt_blocker"):
            self.assertEqual(hb.classify("blocked", r), "approval")
        for r in ("live_blocked_form", "mcp_elicitation_prompt", "dynamic_workflow_prompt"):
            self.assertEqual(hb.classify("blocked", r), "clarify")

    def test_blocked_unknown_rule_is_generic_blocked(self):
        self.assertEqual(hb.classify("blocked", "brand_new_rule"), "blocked")
        self.assertEqual(hb.classify("blocked", None), "blocked")

    def test_unknown_status(self):
        self.assertEqual(hb.classify("unknown", None), "unknown")
        self.assertEqual(hb.classify(None, None), "unknown")

    def test_exit_codes(self):
        self.assertEqual({s: hb.state_exit(s) for s in hb.STATES},
                         {"idle": 0, "busy": 8, "approval": 3, "secret": 4, "clarify": 5,
                          "blocked": 3, "unknown": 7, "dead": 7, "missing": 2})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure** → `AttributeError: ... 'classify'`.

- [ ] **Step 3: Implement**

```python
STATES = ("idle", "busy", "approval", "secret", "clarify", "blocked", "unknown", "dead", "missing")

RULE_STATES = {
    # Hermes manifest (herdr agent-detection hermes.toml)
    "dangerous_command_approval": "approval",
    "confirmation_prompt": "approval",
    "credential_prompt": "secret",
    "clarification_prompt": "clarify",
    # Claude manifest (claude.toml)
    "bash_permission_prompt": "approval",
    "generic_permission_prompt": "approval",
    "legacy_no_prompt_blocker": "approval",
    "live_blocked_form": "clarify",
    "mcp_elicitation_prompt": "clarify",
    "dynamic_workflow_prompt": "clarify",
}

STATE_EXIT = {"idle": EXIT_OK, "busy": EXIT_BUSY, "approval": EXIT_APPROVAL, "secret": EXIT_SECRET,
              "clarify": EXIT_CLARIFY, "blocked": EXIT_APPROVAL, "unknown": EXIT_DEAD,
              "dead": EXIT_DEAD, "missing": EXIT_MISSING}


def classify(agent_status: str | None, matched_rule_id: str | None) -> str:
    if agent_status in ("idle", "done"):
        return "idle"
    if agent_status == "working":
        return "busy"
    if agent_status == "blocked":
        return RULE_STATES.get(matched_rule_id or "", "blocked")
    return "unknown"


def state_exit(state: str) -> int:
    return STATE_EXIT.get(state, EXIT_ERROR)
```

- [ ] **Step 4: Run to verify pass** → 7 tests OK.

- [ ] **Step 5: Commit**

```bash
git add herdrbridge.py tests/test_classify.py
git commit -m "herdrbridge: herdr status + matched rule -> bridge state"
```

---

### Task 5: Reply extraction for Hermes REPL and Claude alt-screen reads

**Files:**
- Modify: `herdrbridge.py`
- Create: `tests/fixtures/hermes_reply.txt`, `tests/fixtures/hermes_before.txt`, `tests/fixtures/claude_reply.txt`
- Create: `tests/test_extract.py`

**Interfaces:**
- Produces: `extract_reply(before: str, after: str, prompt: str, kind: str) -> tuple[str, bool]` where kind is `"hermes"` or `"claude"`; second value is `truncated` (True when the echo anchor was not found and a fallback was used).

- [ ] **Step 1: Create fixtures**

`tests/fixtures/hermes_reply.txt` (captured live 2026-09-01 via `herdr agent read spike --source recent-unwrapped --lines 30`; keep exactly, including box glyphs):

```
│                                   35 tools · 139 skills · 1 MCP servers · /help for       │
│                                   commands                                                │
│                                   ⚠ 1 commit behind — run hermes update to update         │
╰───────────────────────────────────────────────────────────────────────────────────────────╯

Welcome to Hermes Agent! Type your message or /help for commands.
✦ Tip: Smart approval mode uses an LLM to auto-approve safe commands and flag dangerous ones.


────────────────────────────────────────
● Reply with exactly the word OK and nothing else.
Initializing agent...

────────────────────────────────────────

┌─ Reasoning ───────────────────────────────────────────────────────────────────────────────┐
The user wants me to reply with exactly the word "OK" and nothing else. Let me do that.
└───────────────────────────────────────────────────────────────────────────────────────────┘

╭─ ⚕ Hermes  18:16──────────────────────────────────────────────────────────────────────────╮
OK
╰───────────────────────────────────────────────────────────────────────────────────────────╯
 ⚕ qwen3.8-max │ 28.5K/1M │ [░░░░░░░░░░] 3% │ 29s │ ⏲ 7s │ ✓ 0s
─────────────────────────────────────────────────────────────────────────────────────────────
❯
─────────────────────────────────────────────────────────────────────────────────────────────
```

`tests/fixtures/hermes_before.txt`: the first 8 lines of the above (banner through the blank line after the Tip), i.e. the pane before the prompt was sent.

`tests/fixtures/claude_reply.txt` (synthetic, in Claude Code 2.1.x shapes; Task 5 of the claude-bridge plan captures a live transcript and upstreams it here):

```
╭──────────────────────────────────────────────────────────────╮
│ Claude Code v2.1.236                                          │
╰──────────────────────────────────────────────────────────────╯

> Summarize what the file README.md is about in one sentence.

⏺ Read(README.md)
  ⎿  Read 41 lines

⏺ README.md describes a Hermes Agent skill that lets Hermes hold a continuing,
  read-only conversation with Claude Code.

╭──────────────────────────────────────────────────────────────╮
│ ❯                                                            │
╰──────────────────────────────────────────────────────────────╯
  ? for shortcuts
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_extract.py
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import herdrbridge as hb
FIX = os.path.join(os.path.dirname(__file__), "fixtures")
def fx(n):
    with open(os.path.join(FIX, n), encoding="utf-8") as f: return f.read()


class HermesExtractTests(unittest.TestCase):
    def test_reply_from_hermes_box(self):
        reply, trunc = hb.extract_reply(fx("hermes_before.txt"), fx("hermes_reply.txt"),
                                        "Reply with exactly the word OK and nothing else.", "hermes")
        self.assertEqual(reply, "OK"); self.assertFalse(trunc)

    def test_multiline_prompt_anchors_on_first_line(self):
        reply, trunc = hb.extract_reply("", fx("hermes_reply.txt"),
                                        "Reply with exactly the word OK and nothing else.\nsecond line ignored", "hermes")
        self.assertEqual(reply, "OK"); self.assertFalse(trunc)

    def test_missing_anchor_falls_back_to_new_text(self):
        before = fx("hermes_before.txt"); after = fx("hermes_reply.txt")
        reply, trunc = hb.extract_reply(before, after, "totally different prompt", "hermes")
        self.assertTrue(trunc)
        self.assertIn("OK", reply)
        self.assertNotIn("Welcome to Hermes Agent", reply)  # before-text removed

    def test_missing_anchor_and_no_overlap_returns_tail(self):
        reply, trunc = hb.extract_reply("unrelated\nold\n", fx("hermes_reply.txt"), "nope", "hermes")
        self.assertTrue(trunc); self.assertIn("OK", reply)

    def test_box_with_bar_prefixed_lines_is_unwrapped(self):
        after = "● hi\n╭─ ⚕ Hermes  10:00─╮\n│ line one   │\n│ line two   │\n╰──────╯\n❯\n"
        self.assertEqual(hb.extract_reply("", after, "hi", "hermes"), ("line one\nline two", False))


class ClaudeExtractTests(unittest.TestCase):
    def test_reply_after_echo_without_ui_chrome(self):
        reply, trunc = hb.extract_reply("", fx("claude_reply.txt"),
                                        "Summarize what the file README.md is about in one sentence.", "claude")
        self.assertFalse(trunc)
        self.assertTrue(reply.startswith("Read(README.md)"))
        self.assertIn("README.md describes a Hermes Agent skill", reply)
        self.assertNotIn("❯", reply); self.assertNotIn("? for shortcuts", reply)
        self.assertNotIn("Claude Code v", reply)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run to verify failure** → `AttributeError: ... 'extract_reply'`.

- [ ] **Step 4: Implement**

```python
_BOX_EDGE = re.compile(r"^\s*[╭╰┌└├┬┴┼╮╯┐┘─━]{1}")
_HERMES_ECHO = re.compile(r"^\s*●\s*(.*)$")
_HERMES_BOX_OPEN = re.compile(r"^\s*╭─.*Hermes")
_HERMES_BOX_CLOSE = re.compile(r"^\s*╰")
_PROMPT_LINE = re.compile(r"^\s*(│\s*)?❯\s*(│\s*)?$")
_CLAUDE_ECHO = re.compile(r"^\s*>\s*(.*)$")
_CLAUDE_CHROME = re.compile(r"(\? for shortcuts|esc to interrupt|bypass permissions|⏵⏵|shift\+tab to cycle)", re.I)


def _first_line(prompt: str) -> str:
    for ln in prompt.splitlines():
        if ln.strip():
            return ln.strip()
    return prompt.strip()


def _strip_bar(line: str) -> str:
    s = line.strip()
    if s.startswith("│"):
        s = s[1:]
    if s.endswith("│"):
        s = s[:-1]
    return s.strip()


def _hermes_reply(lines: list, start: int) -> str | None:
    """Text inside the last `╭─ … Hermes …╮ … ╰…╯` box after `start`."""
    open_idx = None
    for i in range(start, len(lines)):
        if _HERMES_BOX_OPEN.match(lines[i]):
            open_idx = i
    if open_idx is None:
        return None
    body = []
    for ln in lines[open_idx + 1:]:
        if _HERMES_BOX_CLOSE.match(ln):
            break
        body.append(_strip_bar(ln))
    return "\n".join(body).strip()


def _claude_reply(lines: list, start: int) -> str:
    body = []
    for ln in lines[start:]:
        if _PROMPT_LINE.match(ln) or "❯" in ln:
            break
        if _BOX_EDGE.match(ln) and not ln.strip().startswith("│"):
            # top/bottom edge of the input box or a tool box: skip the edge itself
            continue
        if _CLAUDE_CHROME.search(ln):
            continue
        s = ln.rstrip()
        s = re.sub(r"^\s*⏺\s?", "", s)
        body.append(s)
    text = "\n".join(body)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _new_text(before: str, after: str) -> str | None:
    """Return the part of `after` that follows the last 5 non-empty lines of `before`, or None."""
    tail = [ln for ln in before.splitlines() if ln.strip()][-5:]
    if not tail:
        return None
    a_lines = after.splitlines()
    n = len(tail)
    for i in range(len(a_lines) - n, -1, -1):
        if a_lines[i:i + n] == tail:
            return "\n".join(a_lines[i + n:]).strip()
    return None


def extract_reply(before: str, after: str, prompt: str, kind: str):
    lines = after.splitlines()
    anchor = _first_line(prompt)
    echo_re = _HERMES_ECHO if kind == "hermes" else _CLAUDE_ECHO
    echo_idx = None
    for i, ln in enumerate(lines):
        m = echo_re.match(ln)
        if m and anchor and m.group(1).strip().startswith(anchor[:60]):
            echo_idx = i
    if echo_idx is not None:
        if kind == "hermes":
            boxed = _hermes_reply(lines, echo_idx + 1)
            if boxed is not None:
                return boxed, False
            return _claude_reply(lines, echo_idx + 1), False  # generic: strip chrome after echo
        return _claude_reply(lines, echo_idx + 1), False
    fresh = _new_text(before, after)
    if fresh:
        return fresh, True
    return "\n".join(lines[-120:]).strip(), True
```

- [ ] **Step 5: Run to verify pass**

Run: `python3 -m unittest tests.test_extract -v` → 6 tests OK. If `test_missing_anchor_falls_back_to_new_text` fails on the `Welcome` assertion, check that `hermes_before.txt` really ends with the Tip line and one blank line (the 5-line tail must exist verbatim in `hermes_reply.txt`).

- [ ] **Step 6: Commit**

```bash
git add herdrbridge.py tests/fixtures tests/test_extract.py
git commit -m "herdrbridge: reply extraction for Hermes REPL and Claude alt-screen reads"
```

---

### Task 6: Approval-menu navigation planner

**Files:**
- Modify: `herdrbridge.py`
- Create: `tests/fixtures/hermes_approval_menu.txt`
- Create: `tests/test_menu.py`

**Interfaces:**
- Produces: `parse_menu(visible: str) -> list[MenuRow]` where `MenuRow = (number: int, label: str, selected: bool)`, and `plan_menu_step(visible: str, target: str) -> str | None` returning `"up"`, `"down"`, `"enter"`, or `None` (refuse).

- [ ] **Step 1: Fixture and failing tests**

`tests/fixtures/hermes_approval_menu.txt`:

```
Hermes wants to run a dangerous command:
  rm -rf ~/tmp/foo
⚠ Dangerous command approval
▸ 1. Allow once
  2. Allow for this session
  3. Add to permanent allowlist
  4. Deny
↑/↓ to select · Enter confirm · s show full command
```

```python
# tests/test_menu.py
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import herdrbridge as hb
FIX = os.path.join(os.path.dirname(__file__), "fixtures")
def fx(n):
    with open(os.path.join(FIX, n), encoding="utf-8") as f: return f.read()


class MenuTests(unittest.TestCase):
    def test_parse_rows(self):
        rows = hb.parse_menu(fx("hermes_approval_menu.txt"))
        self.assertEqual([(r.number, r.label, r.selected) for r in rows],
                         [(1, "Allow once", True), (2, "Allow for this session", False),
                          (3, "Add to permanent allowlist", False), (4, "Deny", False)])

    def test_enter_when_cursor_on_target(self):
        self.assertEqual(hb.plan_menu_step(fx("hermes_approval_menu.txt"), "Allow once"), "enter")

    def test_down_to_reach_deny(self):
        self.assertEqual(hb.plan_menu_step(fx("hermes_approval_menu.txt"), "Deny"), "down")

    def test_up_when_target_above(self):
        menu = fx("hermes_approval_menu.txt").replace("▸ 1.", "  1.").replace("  4. Deny", "▸ 4. Deny")
        self.assertEqual(hb.plan_menu_step(menu, "Allow once"), "up")

    def test_refuse_without_cursor(self):
        menu = fx("hermes_approval_menu.txt").replace("▸ ", "  ")
        self.assertIsNone(hb.plan_menu_step(menu, "Deny"))

    def test_refuse_when_target_missing_or_ambiguous(self):
        self.assertIsNone(hb.plan_menu_step(fx("hermes_approval_menu.txt"), "Allow"))  # matches 3 rows
        self.assertIsNone(hb.plan_menu_step(fx("hermes_approval_menu.txt"), "Reject"))

    def test_refuse_when_not_a_menu(self):
        self.assertIsNone(hb.plan_menu_step("just some text\n❯ ", "Deny"))

    def test_cursor_glyph_variants(self):
        menu = fx("hermes_approval_menu.txt").replace("▸ 1.", "❯ 1.")
        self.assertEqual(hb.plan_menu_step(menu, "Allow once"), "enter")
        menu = fx("hermes_approval_menu.txt").replace("▸ 1.", "> 1.")
        self.assertEqual(hb.plan_menu_step(menu, "Allow once"), "enter")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure** → `AttributeError: ... 'parse_menu'`.

- [ ] **Step 3: Implement**

```python
import collections
MenuRow = collections.namedtuple("MenuRow", "number label selected")
_MENU_ROW = re.compile(r"^\s*(?P<cur>[▸❯>])?\s*(?P<num>\d{1,2})\.\s+(?P<label>\S.*?)\s*$")


def parse_menu(visible: str) -> list:
    rows = []
    for ln in visible.splitlines():
        m = _MENU_ROW.match(ln)
        if m:
            rows.append(MenuRow(int(m.group("num")), m.group("label"), bool(m.group("cur"))))
    return rows


def plan_menu_step(visible: str, target: str) -> str | None:
    rows = parse_menu(visible)
    if len(rows) < 2:
        return None
    selected = [i for i, r in enumerate(rows) if r.selected]
    targets = [i for i, r in enumerate(rows) if target.lower() in r.label.lower()]
    if len(selected) != 1 or len(targets) != 1:
        return None
    cur, tgt = selected[0], targets[0]
    if cur == tgt:
        return "enter"
    return "down" if tgt > cur else "up"
```

- [ ] **Step 4: Run to verify pass** → 8 tests OK.

- [ ] **Step 5: Commit**

```bash
git add herdrbridge.py tests/fixtures/hermes_approval_menu.txt tests/test_menu.py
git commit -m "herdrbridge: approval-menu parser and one-step navigation planner"
```

---

### Task 7: Topology, resolve, and common bridge operations

**Files:**
- Modify: `herdrbridge.py`
- Create: `tests/fakes.py`
- Create: `tests/test_bridge_ops.py`

**Interfaces:**
- Produces:
  - `@dataclass class BridgeConfig: workspace_label: str; kind: str; default_cwd: str; exit_command: str = "/exit"; start_timeout_ms: int = 60000; wait_timeout_ms: int = 600000; read_lines: int = 400; shell_settle_s: float = 70.0; poll_s: float = 0.5`
  - `class Bridge(h: Herdr, cfg: BridgeConfig, store: StateStore)` with:
    - `workspace() -> dict` (find-or-create by label; cached)
    - `tabs() -> list[dict]`, `panes() -> list[dict]`, `find_agent(name) -> dict | None`
    - `pane_info(pane_id) -> dict | None`, `pane_is_shell(pane_id) -> bool`
    - `resolve(name) -> tuple[str, object]` → `("live", agent)`, `("restorable", pane_id)`, `("missing", None)`
    - `start(name, launch_args: list[str], fresh: bool = False, resume_flag: str = "--resume", cwd: str | None = None, busy_wait_s: float = 10.0) -> dict` (agent info)
    - `state(name) -> tuple[str, dict | None]` (bridge state, agent)
    - `explain_rule(name) -> str | None`
    - `read(name, lines: int, source: str = "recent-unwrapped") -> str`
    - `visible(name) -> str`
    - `send(name, text, timeout_ms) -> tuple[str, str, bool, str]` → `(state, reply, truncated, dialog)`
    - `wait(name, timeout_ms) -> tuple[str, dict | None]`
    - `answer(name, text) -> str` (new state)
    - `navigate_menu(name, target_label, max_steps=8) -> str` (new state; raises BridgeError on refusal)
    - `stop(name, wait_s=15) -> bool`
    - `gc() -> list[str]` (closed tab ids)
    - `list_sessions() -> list[dict]` (`{name, pane_id, state, session_id}`)
    - `record_session(name, agent) -> None` (persist `agent_session.value`, pane/tab ids)
  - Fake: `tests/fakes.py::FakeHerdr` scripted with `cli_results: dict[str, list]` keyed by the first two CLI words joined with a space (`"agent list"`), popping results in order (last one repeats), `text_results` likewise for `cli_text`, and `calls` recording.

- [ ] **Step 1: Write the fake and failing tests**

```python
# tests/fakes.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import herdrbridge as hb


class FakeHerdr(hb.Herdr):
    def __init__(self, cli_results=None, text_results=None, socket_results=None):
        super().__init__("bridge-test-fake")
        self.cli_results = {k: list(v) for k, v in (cli_results or {}).items()}
        self.text_results = {k: list(v) for k, v in (text_results or {}).items()}
        self.socket_results = {k: list(v) for k, v in (socket_results or {}).items()}
        self.calls = []

    def _pop(self, table, key):
        seq = table.get(key)
        if not seq:
            raise AssertionError("FakeHerdr: no scripted result for %r" % key)
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def cli(self, *args, timeout_s=None):
        key = " ".join(str(a) for a in args[:2])
        self.calls.append(("cli",) + tuple(str(a) for a in args))
        r = self._pop(self.cli_results, key)
        if isinstance(r, Exception):
            raise r
        return r

    def cli_text(self, *args, timeout_s=None):
        key = " ".join(str(a) for a in args[:2])
        self.calls.append(("text",) + tuple(str(a) for a in args))
        r = self._pop(self.text_results, key)
        if isinstance(r, Exception):
            raise r
        return r

    def request(self, method, params, timeout_s=30):
        self.calls.append(("sock", method, params))
        r = self._pop(self.socket_results, method)
        if isinstance(r, Exception):
            raise r
        return r

    def ensure_server(self, wait_s=10, poll_s=0.5):
        self.calls.append(("ensure_server",))


def agent(name="bean", pane="w1:p1", tab="w1:t1", ws="w1", status="idle", session=None, kind="hermes"):
    a = {"agent": kind, "agent_status": status, "name": name, "pane_id": pane, "tab_id": tab,
         "workspace_id": ws, "interactive_ready": True, "focused": False, "revision": 1, "terminal_id": "t"}
    if session:
        a["agent_session"] = {"agent": kind, "kind": "id", "source": "herdr:" + kind, "value": session}
    return a


def ok(type_, **fields):
    d = {"type": type_}; d.update(fields)
    return {"id": "cli", "result": d}


WS = {"workspace_id": "w1", "label": "hermes-bridge", "active_tab_id": "w1:t1"}
```

```python
# tests/test_bridge_ops.py
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import herdrbridge as hb
from fakes import FakeHerdr, agent, ok, WS

CFG = hb.BridgeConfig(workspace_label="hermes-bridge", kind="hermes", default_cwd="/Users/fabzter")


def bridge(h):
    return hb.Bridge(h, CFG, hb.StateStore(tempfile.mkdtemp()))


class WorkspaceTests(unittest.TestCase):
    def test_workspace_found_by_label(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[{"workspace_id": "w9", "label": "other"}, WS])]})
        self.assertEqual(bridge(h).workspace()["workspace_id"], "w1")

    def test_workspace_created_when_missing(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[])],
                       "workspace create": [ok("workspace_created", workspace=WS, tab={"tab_id": "w1:t1"}, root_pane={"pane_id": "w1:p1"})]})
        self.assertEqual(bridge(h).workspace()["workspace_id"], "w1")
        create = [c for c in h.calls if c[:3] == ("cli", "workspace", "create")][0]
        self.assertIn("--no-focus", create); self.assertIn("hermes-bridge", create)


class ResolveTests(unittest.TestCase):
    def test_live_agent_by_name_in_workspace(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean", ws="w1")])]})
        kind, a = bridge(h).resolve("bean")
        self.assertEqual(kind, "live"); self.assertEqual(a["pane_id"], "w1:p1")

    def test_same_name_in_other_workspace_is_not_ours(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean", ws="w7", pane="w7:p1")])],
                       "pane get": [hb.HerdrError("pane_not_found", "x")]})
        b = bridge(h); b.store.save("bean", pane_id="w1:p3")
        self.assertEqual(b.resolve("bean"), ("missing", None))

    def test_restorable_when_stored_pane_is_idle_shell(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[])],
                       "pane get": [ok("pane_info", pane={"pane_id": "w1:p3", "workspace_id": "w1", "agent_status": "unknown"})],
                       "pane process-info": [ok("pane_process_info", process_info={"shell_pid": 1, "foreground_processes": [{"name": "zsh", "argv": ["-zsh"]}]})]})
        b = bridge(h); b.store.save("bean", pane_id="w1:p3", agent_session_id="S1")
        self.assertEqual(b.resolve("bean"), ("restorable", "w1:p3"))

    def test_missing_when_no_stored_pane(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])], "agent list": [ok("agent_list", agents=[])]})
        self.assertEqual(bridge(h).resolve("bean"), ("missing", None))


class StartTests(unittest.TestCase):
    def test_start_missing_creates_tab_and_resumes_stored_session(self):
        started = agent("bean", pane="w1:p5", tab="w1:t3", session="S1")
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w1:t3", "label": "bean"}, root_pane={"pane_id": "w1:p5"})],
                       "agent start": [ok("agent_started", agent=started, argv=["hermes", "chat"])]})
        b = bridge(h); b.store.save("bean", agent_session_id="S1")
        a = b.start("bean", ["chat", "--cli", "--source", "tool"])
        self.assertEqual(a["pane_id"], "w1:p5")
        start = [c for c in h.calls if c[:3] == ("cli", "agent", "start")][0]
        self.assertEqual(start[3:], ("bean", "--kind", "hermes", "--pane", "w1:p5", "--timeout", "60000", "--",
                                     "chat", "--cli", "--source", "tool", "--resume", "S1"))
        self.assertEqual(b.store.load("bean")["pane_id"], "w1:p5")
        self.assertEqual(b.store.load("bean")["agent_session_id"], "S1")

    def test_start_fresh_drops_resume(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w1:t3"}, root_pane={"pane_id": "w1:p5"})],
                       "agent start": [ok("agent_started", agent=agent("bean", pane="w1:p5"))]})
        b = bridge(h); b.store.save("bean", agent_session_id="S1")
        b.start("bean", ["chat"], fresh=True)
        start = [c for c in h.calls if c[:3] == ("cli", "agent", "start")][0]
        self.assertNotIn("--resume", start)
        self.assertNotIn("agent_session_id", b.store.load("bean"))

    def test_start_live_returns_existing_agent_without_starting(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean", session="S9")])]})
        b = bridge(h); a = b.start("bean", ["chat"])
        self.assertEqual(a["name"], "bean")
        self.assertFalse([c for c in h.calls if c[:3] == ("cli", "agent", "start")])
        self.assertEqual(b.store.load("bean")["agent_session_id"], "S9")


class StateTests(unittest.TestCase):
    def test_blocked_uses_explain_rule(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean", status="blocked")])],
                       "agent explain": [{"agent": "hermes", "state": "blocked", "matched_rule": {"id": "credential_prompt"}}]})
        self.assertEqual(bridge(h).state("bean")[0], "secret")

    def test_dead_when_pane_exists_without_agent(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[])],
                       "pane get": [ok("pane_info", pane={"pane_id": "w1:p3", "workspace_id": "w1"})]})
        b = bridge(h); b.store.save("bean", pane_id="w1:p3")
        self.assertEqual(b.state("bean"), ("dead", None))

    def test_missing_when_nothing(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])], "agent list": [ok("agent_list", agents=[])]})
        self.assertEqual(bridge(h).state("bean"), ("missing", None))


class SendTests(unittest.TestCase):
    def setUp(self):
        self.after = "● hello\n╭─ ⚕ Hermes  10:00─╮\nworld\n╰──╯\n❯\n"

    def test_send_prompts_waits_and_extracts(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean")]), ok("agent_list", agents=[agent("bean", session="S2")])],
                       "agent prompt": [ok("agent_prompt", agent=agent("bean", session="S2"))]},
                      {"agent read": ["banner\n", self.after]})
        b = bridge(h)
        state, reply, trunc, dialog = b.send("bean", "hello", 1000)
        self.assertEqual((state, reply, trunc, dialog), ("idle", "world", False, ""))
        prompt = [c for c in h.calls if c[:3] == ("cli", "agent", "prompt")][0]
        self.assertEqual(prompt[3:], ("bean", "hello", "--wait", "--timeout", "1000"))
        self.assertEqual(b.store.load("bean")["agent_session_id"], "S2")

    def test_send_refuses_when_busy(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean", status="working")])]})
        with self.assertRaises(hb.BridgeError) as cm:
            bridge(h).send("bean", "x", 1000)
        self.assertEqual(cm.exception.code, hb.EXIT_BUSY)

    def test_send_stalled_falls_back_to_wait(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean")])],
                       "agent prompt": [hb.HerdrError("agent_prompt_stalled", "no change")],
                       "agent wait": [ok("agent_wait", agent=agent("bean"))]},
                      {"agent read": ["", self.after]})
        state, reply, _, _ = bridge(h).send("bean", "hello", 1000)
        self.assertEqual((state, reply), ("idle", "world"))
        self.assertTrue([c for c in h.calls if c[:3] == ("cli", "agent", "wait")])

    def test_send_ending_blocked_returns_dialog(self):
        blocked = agent("bean", status="blocked")
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean")]), ok("agent_list", agents=[blocked])],
                       "agent prompt": [ok("agent_prompt", agent=blocked)],
                       "agent explain": [{"matched_rule": {"id": "dangerous_command_approval"}}]},
                      {"agent read": ["", "● hello\nHermes wants to run rm\n▸ 1. Allow once\n  2. Deny\n", "▸ 1. Allow once\n  2. Deny\n"]})
        state, reply, _, dialog = bridge(h).send("bean", "hello", 1000)
        self.assertEqual(state, "approval"); self.assertIn("Allow once", dialog)


class MenuNavTests(unittest.TestCase):
    def test_navigate_to_deny_sends_down_then_enter(self):
        menu1 = "▸ 1. Allow once\n  2. Deny\n"; menu2 = "  1. Allow once\n▸ 2. Deny\n"
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean", status="blocked")]), ok("agent_list", agents=[agent("bean", status="working")])],
                       "agent explain": [{"matched_rule": {"id": "dangerous_command_approval"}}],
                       "agent send-keys": [ok("agent_send_keys")]},
                      {"agent read": [menu1, menu2, menu2]})
        b = bridge(h)
        self.assertEqual(b.navigate_menu("bean", "Deny", settle_s=0), "busy")
        keys = [c[4] for c in h.calls if c[:3] == ("cli", "agent", "send-keys")]
        self.assertEqual(keys, ["down", "enter"])

    def test_navigate_refuses_unrecognized_menu(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean", status="blocked")])],
                       "agent explain": [{"matched_rule": {"id": "dangerous_command_approval"}}]},
                      {"agent read": ["some text without a menu\n"]})
        with self.assertRaises(hb.BridgeError):
            bridge(h).navigate_menu("bean", "Deny", settle_s=0)
        self.assertFalse([c for c in h.calls if c[:3] == ("cli", "agent", "send-keys")])


class StopGcListTests(unittest.TestCase):
    def test_stop_sends_exit_then_closes_tab(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean", tab="w1:t2")]), ok("agent_list", agents=[])],
                       "agent prompt": [ok("agent_prompt", agent=agent("bean"))],
                       "tab close": [ok("tab_closed")]})
        b = bridge(h); b.store.save("bean", agent_session_id="S1")
        self.assertTrue(b.stop("bean", wait_s=0))
        prompt = [c for c in h.calls if c[:3] == ("cli", "agent", "prompt")][0]
        self.assertEqual(prompt[3:5], ("bean", "/exit"))
        self.assertEqual([c for c in h.calls if c[:3] == ("cli", "tab", "close")][0][3], "w1:t2")
        self.assertEqual(b.store.load("bean")["agent_session_id"], "S1")  # kept for resume

    def test_stop_missing_is_ok(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])], "agent list": [ok("agent_list", agents=[])]})
        self.assertFalse(bridge(h).stop("bean", wait_s=0))

    def test_gc_closes_shell_only_tabs(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "tab list": [ok("tab_list", tabs=[{"tab_id": "w1:t1", "label": "bean"}, {"tab_id": "w1:t2", "label": "old"}])],
                       "pane list": [ok("pane_list", panes=[{"pane_id": "w1:p1", "tab_id": "w1:t1", "agent": "hermes", "agent_status": "idle"},
                                                             {"pane_id": "w1:p2", "tab_id": "w1:t2", "agent_status": "unknown"}])],
                       "pane process-info": [ok("pane_process_info", process_info={"foreground_processes": [{"name": "zsh"}]})],
                       "tab close": [ok("tab_closed")]})
        self.assertEqual(bridge(h).gc(), ["w1:t2"])

    def test_list_sessions(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "tab list": [ok("tab_list", tabs=[{"tab_id": "w1:t1", "label": "bean"}])],
                       "agent list": [ok("agent_list", agents=[agent("bean", session="S1")])]})
        rows = bridge(h).list_sessions()
        self.assertEqual(rows, [{"name": "bean", "pane_id": "w1:p1", "state": "idle", "session_id": "S1"}])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure** → `AttributeError: ... 'BridgeConfig'`.

- [ ] **Step 3: Implement**

Append to `herdrbridge.py` (add `import dataclasses`):

```python
SHELL_NAMES = {"zsh", "-zsh", "bash", "-bash", "sh", "-sh", "fish", "-fish", "login"}


@dataclasses.dataclass
class BridgeConfig:
    workspace_label: str
    kind: str
    default_cwd: str
    exit_command: str = "/exit"
    start_timeout_ms: int = 60000
    wait_timeout_ms: int = 600000
    read_lines: int = 400


class Bridge:
    def __init__(self, h: Herdr, cfg: BridgeConfig, store: StateStore):
        self.h, self.cfg, self.store = h, cfg, store
        self._ws = None

    # --- topology ----------------------------------------------------------
    def workspace(self) -> dict:
        if self._ws:
            return self._ws
        for ws in self.h.cli("workspace", "list")["result"].get("workspaces", []):
            if ws.get("label") == self.cfg.workspace_label:
                self._ws = ws
                return ws
        res = self.h.cli("workspace", "create", "--cwd", self.cfg.default_cwd,
                         "--label", self.cfg.workspace_label, "--no-focus")["result"]
        self._ws = res["workspace"]
        return self._ws

    def tabs(self) -> list:
        return self.h.cli("tab", "list", "--workspace", self.workspace()["workspace_id"])["result"].get("tabs", [])

    def panes(self) -> list:
        return self.h.cli("pane", "list", "--workspace", self.workspace()["workspace_id"])["result"].get("panes", [])

    def agents(self) -> list:
        ws = self.workspace()["workspace_id"]
        return [a for a in self.h.cli("agent", "list")["result"].get("agents", []) if a.get("workspace_id") == ws]

    def find_agent(self, name: str) -> dict | None:
        for a in self.agents():
            if a.get("name") == name:
                return a
        return None

    def pane_info(self, pane_id: str) -> dict | None:
        try:
            return self.h.cli("pane", "get", pane_id)["result"].get("pane")
        except HerdrError as e:
            if e.herdr_code in ("pane_not_found", "not_found"):
                return None
            raise

    def pane_is_shell(self, pane_id: str) -> bool:
        info = self.h.cli("pane", "process-info", "--pane", pane_id)["result"].get("process_info", {})
        fg = info.get("foreground_processes") or []
        return all(os.path.basename(str(p.get("name", ""))) in SHELL_NAMES for p in fg)

    def _create_tab(self, name: str, cwd: str) -> tuple:
        res = self.h.cli("tab", "create", "--workspace", self.workspace()["workspace_id"],
                         "--cwd", cwd, "--label", name, "--no-focus")["result"]
        return res["tab"]["tab_id"], res["root_pane"]["pane_id"]

    # --- session identity ---------------------------------------------------
    def record_session(self, name: str, agent: dict) -> None:
        sess = (agent.get("agent_session") or {}).get("value")
        fields = {"pane_id": agent.get("pane_id"), "tab_id": agent.get("tab_id")}
        if sess:
            fields["agent_session_id"] = sess
        self.store.save(name, **fields)

    def resolve(self, name: str):
        a = self.find_agent(name)
        if a:
            return "live", a
        st = self.store.load(name)
        pane_id = st.get("pane_id")
        if pane_id:
            info = self.pane_info(pane_id)
            if info and info.get("workspace_id") == self.workspace()["workspace_id"] and self.pane_is_shell(pane_id):
                return "restorable", pane_id
        return "missing", None

    # --- lifecycle ------------------------------------------------------------
    def start(self, name: str, launch_args: list, fresh: bool = False, resume_flag: str = "--resume",
              cwd: str | None = None) -> dict:
        kind, obj = self.resolve(name)
        if kind == "live":
            self.record_session(name, obj)
            return obj
        st = self.store.load(name)
        if fresh:
            self.store.save(name, agent_session_id=None)
            st.pop("agent_session_id", None)
        if kind == "restorable":
            pane_id = obj
        else:
            tab_id, pane_id = self._create_tab(name, cwd or st.get("cwd") or self.cfg.default_cwd)
            self.store.save(name, tab_id=tab_id, pane_id=pane_id, cwd=cwd or st.get("cwd") or self.cfg.default_cwd)
        args = list(launch_args)
        if st.get("agent_session_id"):
            args += [resume_flag, st["agent_session_id"]]
        try:
            res = self.h.cli("agent", "start", name, "--kind", self.cfg.kind, "--pane", pane_id,
                             "--timeout", str(self.cfg.start_timeout_ms), "--", *args,
                             timeout_s=self.cfg.start_timeout_ms / 1000.0 + 30)["result"]
            agent = res["agent"]
        except HerdrError as e:
            if e.herdr_code != "agent_not_ready":
                raise
            agent = self.find_agent(name) or {"pane_id": pane_id, "name": name, "agent_status": "blocked"}
        self.record_session(name, agent)
        return agent

    def explain_rule(self, name: str) -> str | None:
        try:
            out = self.h.cli("agent", "explain", name, "--json")
        except HerdrError:
            return None
        rule = out.get("matched_rule") or (out.get("result") or {}).get("matched_rule")
        return rule.get("id") if isinstance(rule, dict) else None

    def state(self, name: str):
        a = self.find_agent(name)
        if a:
            status = a.get("agent_status")
            rule = self.explain_rule(name) if status == "blocked" else None
            return classify(status, rule), a
        st = self.store.load(name)
        if st.get("pane_id") and self.pane_info(st["pane_id"]):
            return "dead", None
        return "missing", None

    # --- I/O --------------------------------------------------------------------
    def read(self, name: str, lines: int | None = None, source: str = "recent-unwrapped") -> str:
        return self.h.cli_text("agent", "read", name, "--source", source, "--lines", str(lines or self.cfg.read_lines))

    def visible(self, name: str) -> str:
        return self.h.cli_text("agent", "read", name, "--source", "visible")

    def wait(self, name: str, timeout_ms: int):
        self.h.cli("agent", "wait", name, "--timeout", str(timeout_ms), timeout_s=timeout_ms / 1000.0 + 30)
        return self.state(name)

    def send(self, name: str, text: str, timeout_ms: int):
        state, agent = self.state(name)
        if state != "idle":
            raise BridgeError("session %r is %s; refusing to send" % (name, state), state_exit(state))
        before = self.read(name)
        try:
            self.h.cli("agent", "prompt", name, text, "--wait", "--timeout", str(timeout_ms),
                       timeout_s=timeout_ms / 1000.0 + 30)
        except HerdrError as e:
            if e.herdr_code == "agent_prompt_stalled":
                self.h.cli("agent", "wait", name, "--timeout", str(timeout_ms), timeout_s=timeout_ms / 1000.0 + 30)
            elif e.herdr_code == "timeout":
                raise BridgeError("timed out after %dms waiting for %r; it may still be working" % (timeout_ms, name), EXIT_TIMEOUT)
            else:
                raise
        after = self.read(name)
        state, agent = self.state(name)
        if agent:
            self.record_session(name, agent)
        reply, truncated = extract_reply(before, after, text, self.cfg.kind)
        dialog = self.visible(name) if state in ("approval", "secret", "clarify", "blocked") else ""
        return state, reply, truncated, dialog

    def answer(self, name: str, text: str) -> str:
        state, agent = self.state(name)
        if state != "clarify":
            raise BridgeError("session %r is %s, not clarify; refusing to answer" % (name, state), state_exit(state))
        self.h.cli("pane", "send-text", agent["pane_id"], text)
        self.h.cli("pane", "send-keys", agent["pane_id"], "enter")
        time.sleep(1.0)
        return self.state(name)[0]

    def navigate_menu(self, name: str, target_label: str, max_steps: int = 8, settle_s: float = 0.4) -> str:
        state, _ = self.state(name)
        if state != "approval":
            raise BridgeError("session %r is %s, not approval; refusing" % (name, state), state_exit(state))
        for _ in range(max_steps):
            step = plan_menu_step(self.visible(name), target_label)
            if step is None:
                raise BridgeError("approval menu not recognized or %r not found exactly once; refusing to act" % target_label)
            self.h.cli("agent", "send-keys", name, step)
            time.sleep(settle_s)
            if step == "enter":
                return self.state(name)[0]
        raise BridgeError("could not reach %r within %d keystrokes; refusing" % (target_label, max_steps))

    def stop(self, name: str, wait_s: float = 15) -> bool:
        a = self.find_agent(name)
        tab_id = None
        if a:
            tab_id = a.get("tab_id")
            try:
                self.h.cli("agent", "prompt", name, self.cfg.exit_command)
            except HerdrError:
                pass
            deadline = time.time() + wait_s
            while time.time() < deadline and self.find_agent(name):
                time.sleep(0.5)
        else:
            st = self.store.load(name)
            if st.get("pane_id") and self.pane_info(st["pane_id"]):
                tab_id = st.get("tab_id")
        if not tab_id:
            return False
        try:
            self.h.cli("tab", "close", tab_id)
        except HerdrError as e:
            if e.herdr_code not in ("not_found", "tab_not_found"):
                raise
        self.store.save(name, pane_id=None, tab_id=None)
        return True

    def gc(self) -> list:
        live_tabs = {a.get("tab_id") for a in self.agents()}
        closed = []
        panes_by_tab = {}
        for p in self.panes():
            panes_by_tab.setdefault(p.get("tab_id"), []).append(p)
        for tab in self.tabs():
            tid = tab.get("tab_id")
            if tid in live_tabs:
                continue
            ps = panes_by_tab.get(tid, [])
            if ps and all(not p.get("agent") and self.pane_is_shell(p["pane_id"]) for p in ps):
                self.h.cli("tab", "close", tid)
                closed.append(tid)
        return closed

    def list_sessions(self) -> list:
        rows = []
        agents = {a.get("name"): a for a in self.agents()}
        for tab in self.tabs():
            name = tab.get("label")
            a = agents.get(name)
            if a:
                st = classify(a.get("agent_status"), self.explain_rule(name) if a.get("agent_status") == "blocked" else None)
                sid = (a.get("agent_session") or {}).get("value") or self.store.load(name).get("agent_session_id")
                rows.append({"name": name, "pane_id": a.get("pane_id"), "state": st, "session_id": sid})
            else:
                rows.append({"name": name, "pane_id": None, "state": "dead", "session_id": self.store.load(name).get("agent_session_id")})
        return rows
```

Note for `test_gc_closes_shell_only_tabs`: `agents()` there calls `agent list`, which the fake does not script; add `"agent list": [ok("agent_list", agents=[])]` to that test's fake if the assertion error says so (the test as written relies on `pane list` alone; adjust the test, not the code).

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m unittest tests.test_bridge_ops -v` → all OK (22 tests). Fix scripted-result gaps in the tests where `FakeHerdr` raises `AssertionError: no scripted result`, never by adding untested branches to the library.

- [ ] **Step 5: Commit**

```bash
git add herdrbridge.py tests/fakes.py tests/test_bridge_ops.py
git commit -m "herdrbridge: topology, resolve, start/state/send/answer/menu/stop/gc/list"
```

---

## Self-review notes

- Spec coverage: §3.1–3.2 Task 2; §3.4 Task 3; §3.5–3.6 Task 7 (`resolve`, `start`, `record_session`); §3.7 Task 4; §3.8 Tasks 5, 7; §3.9 Tasks 6, 7; §3.10 Task 7; §3.11 Tasks 1, 4. CLI surfaces, live runs and docs live in the downstream plans.
- Type consistency: `Bridge.send` returns `(state, reply, truncated, dialog)`; `navigate_menu(name, target_label, max_steps, settle_s)`; `stop(name, wait_s)`; `StateStore.save(name, **fields)`; `Herdr(session, bin, runner, spawner, socket_path)`.
- Task 7 also adds `README.md` (three paragraphs: purpose, API overview by section, how downstream repos vendor it with `tools/sync-lib.sh`) before its commit.
