"""herdrbridge — shared plumbing for the Claude<->Hermes bridges on herdr.

Canonical repo: fabzter/herdrbridge. fabzter/hermes-bridge and
fabzter/hermes-claude-bridge vendor pinned copies via tools/sync-lib.sh; change here first.
Stdlib only; Python 3.9+.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import socket
import subprocess
import time
import uuid

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
            raise HerdrError("timeout", "herdr %s exceeded %ss" % (" ".join(argv[1:3]), timeout_s))
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
        log_dir = os.path.dirname(self.socket_path)
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "herdr-server.log")
        with open(log_path, "ab") as log:
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
