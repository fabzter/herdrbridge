# herdrbridge follow-up — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the items the user un-deferred after the first release: bounded log growth, a polling `answer`, workspace-cache invalidation, name validation at every `Bridge` entry point, a status wait with a polling fallback when the herdr socket drops, and a cleaner Claude fixture.

**Architecture:** All changes are additive to `herdrbridge.py` (public names stable; new optional parameters and new functions). Tests inject sleeps and use `FakeHerdr`. Downstream repos re-vendor at the resulting commit.

**Tech Stack:** Python 3.9+ stdlib only.

**Spec:** `docs/superpowers/specs/2026-09-01-herdr-bridges-design.md` (§3.2 server auto-start, §3.9 dialogs, §3.3 topology, §6 error handling incl. "socket disconnects fall back to polling").

## Global Constraints

- Repo root `/Users/fabzter/src/herdrbridge`; `herdrbridge.py` at the root; tests under `tests/` (`tests/fakes.py` provides `FakeHerdr`, `agent`, `ok`, `WS`; the fake repeats its last scripted value; on "no scripted result" fix the test scripting, never add untested branches).
- Python 3 stdlib only; Python 3.9 compatible (`from __future__ import annotations`, no `match`, no `X | Y` outside annotations, no subscripted builtins at runtime).
- Public API stability: existing names/signatures unchanged; new parameters have defaults. Downstream (`fabzter/hermes-bridge`, `fabzter/hermes-claude-bridge`) vendor this file.
- No real sleeping in tests: every wait uses an injectable sleep (module-level `_sleep = time.sleep`, patched in tests) or `poll_s=0`.
- `python3 -m unittest discover -s tests -v` pristine under `python3`, `/usr/bin/python3` (3.9), and `/Users/fabzter/.hermes/hermes-agent/venv/bin/python` before every commit.
- Commit messages carry no attribution footers of any kind; no AI-authorship text anywhere. Push after each task (`git push origin main`).

## File structure

| File | Responsibility |
|---|---|
| `herdrbridge.py` | `rotate_log`, `ensure_server` spawn log, `Bridge.answer` polling, `Bridge.workspace` invalidation, `validate_name` at entry points, `Bridge.wait_status` |
| `tests/test_logrotate.py`, `tests/test_bridge_ops.py`, `tests/test_herdr_client.py`, `tests/test_names.py` | Tests per task |
| `tests/fixtures/claude_reply.txt` | Stale `MultiEdit` warning lines removed |
| `README.md` | API additions documented |

---

### Task 1: Log rotation and a private spawn log for `ensure_server`

**Files:** Modify `herdrbridge.py`; Create `tests/test_logrotate.py`; Modify `tests/test_herdr_client.py`.

**Interfaces — Produces:** `rotate_log(path: str, max_bytes: int = 5 * 1024 * 1024, keep: int = 2) -> bool` (True when a rotation happened); `Herdr.ensure_server` writes the spawned server's stdio to `<socket dir>/herdr-server.spawn.log` (herdr writes its own `herdr-server.log` in that directory — the bridge must not share that file) and calls `rotate_log` on it before opening.

- [ ] **Step 1: Failing tests**

```python
# tests/test_logrotate.py
import os, shutil, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import herdrbridge as hb


class RotateLogTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.p = os.path.join(self.d, "x.log")

    def write(self, path, size):
        with open(path, "wb") as f: f.write(b"x" * size)

    def test_missing_file_is_noop(self):
        self.assertFalse(hb.rotate_log(self.p, max_bytes=10))

    def test_under_threshold_is_noop(self):
        self.write(self.p, 5); self.assertFalse(hb.rotate_log(self.p, max_bytes=10)); self.assertTrue(os.path.exists(self.p))

    def test_over_threshold_rotates_and_keeps_n(self):
        self.write(self.p, 20); self.assertTrue(hb.rotate_log(self.p, max_bytes=10, keep=2))
        self.assertFalse(os.path.exists(self.p)); self.assertTrue(os.path.exists(self.p + ".1"))
        self.write(self.p, 20); hb.rotate_log(self.p, max_bytes=10, keep=2)
        self.write(self.p, 20); hb.rotate_log(self.p, max_bytes=10, keep=2)
        self.assertTrue(os.path.exists(self.p + ".1")); self.assertTrue(os.path.exists(self.p + ".2"))
        self.assertFalse(os.path.exists(self.p + ".3"))
```

Add to `tests/test_herdr_client.py::SocketTests`:

```python
    def test_ensure_server_uses_private_spawn_log_and_rotates(self):
        spawned = []
        sock = os.path.join(self.tmp, "missing.sock")
        big = os.path.join(self.tmp, "herdr-server.spawn.log")
        with open(big, "wb") as f: f.write(b"x" * (6 * 1024 * 1024))
        h = hb.Herdr("t", socket_path=sock, spawner=lambda *a, **k: spawned.append(k))
        with self.assertRaises(hb.ServerUnavailable):
            h.ensure_server(wait_s=0.05, poll_s=0.01)
        self.assertEqual(spawned[0]["stdout"].name, big)
        self.assertTrue(os.path.exists(big + ".1"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "herdr-server.log")))
```

- [ ] **Step 2: Run → fail** (`AttributeError: rotate_log`).
- [ ] **Step 3: Implement**

```python
def rotate_log(path: str, max_bytes: int = 5 * 1024 * 1024, keep: int = 2) -> bool:
    """Rotate `path` to `.1`, `.1` to `.2`, … when it exceeds `max_bytes`. Returns True if rotated."""
    try:
        if os.path.getsize(path) <= max_bytes:
            return False
    except OSError:
        return False
    for i in range(keep, 0, -1):
        src = path if i == 1 else "%s.%d" % (path, i - 1)
        dst = "%s.%d" % (path, i)
        if os.path.exists(src):
            os.replace(src, dst)
    return True
```

In `ensure_server`: `log_path = os.path.join(log_dir, "herdr-server.spawn.log")`; call `rotate_log(log_path)` before `open(log_path, "ab")`.

- [ ] **Step 4: Run → pass.**  - [ ] **Step 5: Commit** `herdrbridge: rotate_log; ensure_server writes a private rotated spawn log`; push.

---

### Task 2: `answer` polls until the agent leaves `clarify`

**Files:** Modify `herdrbridge.py` (`Bridge.answer`), `tests/test_bridge_ops.py`.

**Interfaces — Produces:** `Bridge.answer(name, text, settle_s: float = 5.0, poll_s: float = 0.25) -> str`; module-level `_sleep = time.sleep` used by every wait in the file (tests patch `hb._sleep`).

- [ ] **Step 1: Failing tests** (in `AnswerTests`): with `hb._sleep` patched to a recorder, script `agent list` → `[clarify-agent, clarify-agent, clarify-agent, working-agent]` and `agent explain` → `clarification_prompt`; `answer("bean", "yes", settle_s=1.0, poll_s=0.1)` returns `"busy"` and the recorder shows ≤ 10 sleeps; with all scripted states clarify, it raises `BridgeError` with code 5 after the deadline (no real time passes because `_sleep` is patched and the deadline uses a patched `hb._now` or the loop counts polls: implement with `deadline = _now() + settle_s` where `_now = time.time` is also module-level and patched in tests to advance by `poll_s` per call).
- [ ] **Step 2: Run → fail.**  - [ ] **Step 3: Implement**

```python
_sleep = time.sleep
_now = time.time

    def answer(self, name: str, text: str, settle_s: float = 5.0, poll_s: float = 0.25) -> str:
        validate_name(name)
        state, agent = self.state(name)
        if state != "clarify":
            raise BridgeError(...)  # unchanged
        self.h.cli("pane", "send-text", agent["pane_id"], text)
        self.h.cli("pane", "send-keys", agent["pane_id"], "enter")
        deadline = _now() + settle_s
        while True:
            _sleep(poll_s)
            new_state, _ = self.state(name)
            if new_state != "clarify":
                return new_state
            if _now() >= deadline:
                raise BridgeError("answer to %r did not register; agent still in clarify" % name, EXIT_CLARIFY)
```

Replace every `time.sleep(` in the file with `_sleep(` and `time.time()` deadline reads with `_now()`; existing tests that pass `settle_s=0`/`poll_s=0` still pass.

- [ ] **Step 4: Run → pass.**  - [ ] **Step 5: Commit** `herdrbridge: answer polls until the agent leaves clarify; injectable sleep/clock`; push.

---

### Task 3: Workspace cache invalidation

**Files:** Modify `herdrbridge.py` (`Bridge.workspace`, `tabs`, `panes`, `agents`, `_create_tab`), `tests/test_bridge_ops.py`.

**Interfaces — Produces:** `Bridge.workspace(refresh: bool = False) -> dict`; `Bridge.invalidate_workspace() -> None`; internal `_with_workspace_retry(fn)` that runs `fn(workspace_id)` and, on `HerdrError` with `herdr_code in ("workspace_not_found", "not_found")`, invalidates the cache and retries once.

- [ ] **Step 1: Failing tests:** `test_tabs_retries_after_workspace_vanished`: `workspace list` scripted `[ws w1, ws w2]`, `tab list` scripted `[HerdrError("workspace_not_found", "gone"), ok(tabs=[...])]` → `tabs()` returns the tabs and the second `tab list` call used `--workspace w2`; `test_invalidate_workspace_forces_refresh`: after `workspace()`, `invalidate_workspace()`, `workspace()` calls `workspace list` again; `test_workspace_refresh_flag`.
- [ ] **Step 2: Run → fail.**  - [ ] **Step 3: Implement** (`workspace(refresh=False)` skips the cache when `refresh`; `tabs/panes/agents/_create_tab` go through `_with_workspace_retry`).
- [ ] **Step 4: Run → pass.**  - [ ] **Step 5: Commit** `herdrbridge: workspace cache invalidation with one retry on workspace_not_found`; push.

---

### Task 4: Name validation at every `Bridge` entry point

**Files:** Modify `herdrbridge.py`, `tests/test_bridge_ops.py`.

- [ ] **Step 1: Failing test:** for each of `resolve, start, state, read, visible, wait, send, answer, navigate_menu, stop, record_session` calling with name `"../evil"` raises `UsageError` and `FakeHerdr.calls` stays empty (parametrize in one test with a loop; `start` gets `launch_args=[]`, `send` gets `("x", 10)`, `record_session` gets `{}`).
- [ ] **Step 2: Run → fail.**  - [ ] **Step 3: Implement:** first statement of each method: `validate_name(name)`.
- [ ] **Step 4: Run → pass.**  - [ ] **Step 5: Commit** `herdrbridge: validate session names at every Bridge entry point`; push.

---

### Task 5: `wait_status` with polling fallback

**Files:** Modify `herdrbridge.py`, `tests/test_bridge_ops.py`.

**Interfaces — Produces:** `Bridge.wait_status(name, until=("idle", "done", "blocked"), timeout_ms: int = 600000, poll_s: float = 2.0) -> tuple[str, dict | None]` — primary: `herdr agent wait NAME --until U… --timeout MS` (server-side); if that raises `HerdrError` whose code is not `timeout`/`agent_not_found`/`agent_not_running` (e.g. `closed`, `bad_json`, `error`, `server_not_running`) or an `OSError`, fall back to polling `self.state(name)` every `poll_s` until the classified herdr status is in `until` (map bridge states back: idle→idle/done, busy→working, approval/secret/clarify/blocked→blocked) or the deadline (`_now()` based) passes → `BridgeError(EXIT_TIMEOUT)`. Returns `self.state(name)`.

- [ ] **Step 1: Failing tests:** happy path (`agent wait` ok → returns state); fallback (`agent wait` raises `HerdrError("closed", ...)`, `agent list` scripted `[working, working, idle]` → returns `("idle", agent)` with two sleeps); fallback timeout (`_now` patched to jump past the deadline → `BridgeError` code 6); `timeout` from the CLI propagates as code 6 without polling.
- [ ] **Step 2: Run → fail.**  - [ ] **Step 3: Implement** with `_sleep`/`_now`.
- [ ] **Step 4: Run → pass.**  - [ ] **Step 5: Commit** `herdrbridge: wait_status with polling fallback when the herdr socket drops`; push.

---

### Task 6: Fixture cleanup, README, spec note

**Files:** Modify `tests/fixtures/claude_reply.txt` (remove the two historical lines about the `MultiEdit` deny rule / "matches no known tool"; re-run `tests/test_extract.py`), `README.md` (document `rotate_log`, `answer` polling, `workspace(refresh)`, `wait_status`, the spawn log path, and that names are validated at every entry point), `docs/superpowers/specs/2026-09-01-herdr-bridges-design.md` (§3.9: "`--yolo`/bypass modes are permitted only when the user explicitly requests them for a session; the default remains prompting").

- [ ] Steps: edit; `python3 -m unittest discover -s tests -v` under all three interpreters; commit `herdrbridge: docs for the follow-up APIs; drop stale fixture lines`; push; print the final sha (downstream plans pin it).
