"""herdrbridge — shared plumbing for the Claude<->Hermes bridges on herdr.

Canonical repo: fabzter/herdrbridge. fabzter/hermes-bridge and
fabzter/hermes-claude-bridge vendor pinned copies via tools/sync-lib.sh; change here first.
Stdlib only; Python 3.9+.
"""
from __future__ import annotations

import collections
import dataclasses
import datetime
import json
import os
import re
import socket
import subprocess
import time
import uuid

SESSION_DEFAULT = "agents"
SESSION_NAME = SESSION_DEFAULT
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
    "server_not_running": EXIT_SERVER,
    "tab_not_found": EXIT_MISSING,
    "workspace_not_found": EXIT_MISSING,
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
        timeout_s = 30 if timeout_s is None else timeout_s
        argv = [self.bin] + [str(a) for a in args]
        try:
            cp = self._runner(argv, env=self.env(), capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            raise HerdrError("timeout", "herdr %s exceeded %ss" % (" ".join(argv[1:3]), timeout_s))
        if cp.returncode == 0:
            return cp
        if cp.returncode == 2:
            detail = (cp.stderr or "").strip()
            prefix = ("%s: " % detail) if detail else "herdr usage error: "
            raise HerdrError("usage", prefix + " ".join(argv))
        try:
            err = json.loads((cp.stderr or "").strip().splitlines()[-1])["error"]
            raise HerdrError(str(err.get("code", "error")), str(err.get("message", "")))
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
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
        try:
            s.connect(self.socket_path)
        except Exception:
            s.close()
            raise
        return s

    @staticmethod
    def _parse_response(line: bytes) -> dict:
        msg = json.loads(line.decode("utf-8"))
        if "error" in msg:
            err = msg["error"] or {}
            raise HerdrError(str(err.get("code", "error")), str(err.get("message", "")))
        return msg

    def request(self, method: str, params: dict, timeout_s: float = 30) -> dict:
        try:
            s = self._connect(timeout_s)
        except (FileNotFoundError, ConnectionRefusedError) as e:
            raise ServerUnavailable("herdr socket unavailable at %s: %s" % (self.socket_path, e))
        try:
            rid = "bridge-%s" % uuid.uuid4().hex[:8]
            s.sendall((json.dumps({"id": rid, "method": method, "params": params}) + "\n").encode("utf-8"))
            f = s.makefile("rb")
            line = f.readline()
            if not line:
                raise HerdrError("closed", "herdr closed the socket without answering %s" % method)
            return self._parse_response(line).get("result", {})
        except socket.timeout:
            raise HerdrError("timeout", "herdr %s timed out after %ss" % (method, timeout_s))
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
                if not line.strip():
                    continue
                try:
                    event = json.loads(line.decode("utf-8"))
                except ValueError:
                    break  # truncated/partial line (e.g. socket closed mid-write): end the stream
                yield event
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
            except (OSError, HerdrError, ValueError, ServerUnavailable):
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
                result = self.save(name, agent_session_id=sid, migrated_from="session-id")
                os.replace(legacy, legacy + ".migrated")
                return result
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
        deleted = False
        if os.path.exists(p):
            os.remove(p)
            deleted = True
        legacy = os.path.join(self.dir, "%s.session-id" % name)
        if os.path.exists(legacy):
            os.remove(legacy)
            deleted = True
        return deleted

    def names(self) -> list:
        if not os.path.isdir(self.dir):
            return []
        return sorted(f[:-5] for f in os.listdir(self.dir) if f.endswith(".json"))


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


# --- reply extraction for Hermes REPL and Claude alt-screen reads --------

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
    # Track (original_index, line_content) for non-empty lines to allow searching with blank-line gaps
    a_nonempty = [(i, ln) for i, ln in enumerate(a_lines) if ln.strip()]
    n = len(tail)
    for i in range(len(a_nonempty) - n, -1, -1):
        if [ln for _, ln in a_nonempty[i:i + n]] == tail:
            # Found all 5 lines; return everything after the last one
            last_idx = a_nonempty[i + n - 1][0]
            return "\n".join(a_lines[last_idx + 1:]).strip()
    return None


def extract_reply(before: str, after: str, prompt: str, kind: str):
    lines = after.splitlines()
    anchor = _first_line(prompt)
    echo_re = _HERMES_ECHO if kind == "hermes" else _CLAUDE_ECHO
    echo_idx = None
    for i, ln in enumerate(lines):
        m = echo_re.match(ln)
        if m and anchor:
            echo = m.group(1).strip()
            # Exact match for short prompts; prefix match for long ones
            if len(anchor) <= 60:
                matches = echo == anchor
            else:
                matches = echo.startswith(anchor[:60])
            if matches:
                echo_idx = i
    if echo_idx is not None:
        if kind == "hermes":
            boxed = _hermes_reply(lines, echo_idx + 1)
            if boxed is not None:
                return boxed, False
            return _claude_reply(lines, echo_idx + 1), True  # generic fallback: no Hermes box found
        return _claude_reply(lines, echo_idx + 1), False
    fresh = _new_text(before, after)
    if fresh is not None:
        return fresh, True
    return "\n".join(lines[-120:]).strip(), True


# --- approval menu navigation planner ----

MenuRow = collections.namedtuple("MenuRow", "number label selected")
_MENU_ROW = re.compile(r"^\s*(?P<cur>[▸❯>])?\s*(?P<num>\d{1,2})\.\s+(?P<label>\S.*?)\s*$")
_MENU_FOOTER = re.compile(r"(↑/↓|enter confirm|enter to confirm|show full command)", re.I)


def parse_menu(visible: str) -> list:
    """Parse a Hermes approval menu; returns [] unless the screen ends in a menu footer with contiguous numbered rows above it."""
    lines = visible.splitlines()
    footer_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if _MENU_FOOTER.search(lines[i]):
            footer_idx = i
            break
    if footer_idx is None:
        return []
    rows = []
    for i in range(footer_idx - 1, -1, -1):
        m = _MENU_ROW.match(lines[i])
        if m:
            rows.append(MenuRow(int(m.group("num")), m.group("label"), bool(m.group("cur"))))
        elif lines[i].strip():
            break
    return list(reversed(rows))


def plan_menu_step(visible: str, target: str) -> str | None:
    """Plan one navigation step to reach target; returns None unless the screen ends in a menu footer with contiguous numbered rows above it."""
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


# --- topology, resolve and common bridge operations ----------------------

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
        matches = [a for a in self.agents() if a.get("name") == name]
        if len(matches) > 1:
            raise BridgeError("multiple live agents named %r; refusing to guess" % name, EXIT_ERROR)
        return matches[0] if matches else None

    def pane_info(self, pane_id: str) -> dict | None:
        try:
            return self.h.cli("pane", "get", pane_id)["result"].get("pane")
        except HerdrError as e:
            if e.herdr_code in ("pane_not_found", "not_found"):
                return None
            raise

    def pane_is_shell(self, pane_id: str) -> bool:
        try:
            info = self.h.cli("pane", "process-info", "--pane", pane_id)["result"].get("process_info", {})
        except HerdrError as e:
            if e.herdr_code in ("pane_not_found", "not_found"):
                return False
            raise
        fg = info.get("foreground_processes") or []
        return bool(fg) and all(os.path.basename(str(p.get("name", ""))) in SHELL_NAMES for p in fg)

    def _create_tab(self, name: str, cwd: str) -> tuple:
        res = self.h.cli("tab", "create", "--workspace", self.workspace()["workspace_id"],
                         "--cwd", cwd, "--label", name, "--no-focus")["result"]
        return res["tab"]["tab_id"], res["root_pane"]["pane_id"]

    # --- session identity ---------------------------------------------------
    def record_session(self, name: str, agent: dict) -> None:
        sess = (agent.get("agent_session") or {}).get("value")
        fields = {}
        if agent.get("pane_id"):
            fields["pane_id"] = agent["pane_id"]
        if agent.get("tab_id"):
            fields["tab_id"] = agent["tab_id"]
        if sess:
            fields["agent_session_id"] = sess
        if not fields:
            return
        self.store.save(name, **fields)

    def resolve(self, name: str):
        a = self.find_agent(name)
        if a:
            return "live", a
        st = self.store.load(name)
        pane_id = st.get("pane_id")
        if pane_id:
            info = self.pane_info(pane_id)
            if (info and not info.get("agent")
                    and info.get("workspace_id") == self.workspace()["workspace_id"]
                    and self.pane_is_shell(pane_id)):
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
        result = out.get("result") or {}
        for container in (out, result, result.get("explain") or {}, out.get("explain") or {}):
            rule = container.get("matched_rule")
            if isinstance(rule, dict):
                return rule.get("id")
        return None

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
            raise BridgeError("session %r is %s; refusing to send" % (name, state), state_exit(state) or EXIT_ERROR)
        before = self.read(name)
        blocked_before_input = False
        try:
            self.h.cli("agent", "prompt", name, text, "--wait", "--timeout", str(timeout_ms),
                       timeout_s=timeout_ms / 1000.0 + 30)
        except HerdrError as e:
            if e.herdr_code == "agent_prompt_stalled":
                self.h.cli("agent", "wait", name, "--timeout", str(timeout_ms), timeout_s=timeout_ms / 1000.0 + 30)
            elif e.herdr_code == "agent_blocked":
                blocked_before_input = True  # agent was blocked before it saw any input
            elif e.herdr_code == "timeout":
                raise BridgeError("timed out after %dms waiting for %r; it may still be working" % (timeout_ms, name), EXIT_TIMEOUT)
            else:
                raise
        after = self.read(name)
        state, agent = self.state(name)
        if agent:
            self.record_session(name, agent)
        dialog = self.visible(name) if state in ("approval", "secret", "clarify", "blocked") else ""
        if blocked_before_input:
            return state, "", False, "MESSAGE NOT DELIVERED: agent was blocked before input\n" + dialog
        reply, truncated = extract_reply(before, after, text, self.cfg.kind)
        return state, reply, truncated, dialog

    def answer(self, name: str, text: str, settle_s: float = 1.0) -> str:
        state, agent = self.state(name)
        if state != "clarify":
            raise BridgeError("session %r is %s, not clarify; refusing to answer" % (name, state), state_exit(state) or EXIT_ERROR)
        self.h.cli("pane", "send-text", agent["pane_id"], text)
        self.h.cli("pane", "send-keys", agent["pane_id"], "enter")
        time.sleep(settle_s)
        new_state, _ = self.state(name)
        if new_state == "clarify":
            raise BridgeError("answer to %r did not register; agent still in clarify" % name, EXIT_CLARIFY)
        return new_state

    def navigate_menu(self, name: str, target_label: str, max_steps: int = 8, settle_s: float = 0.4) -> str:
        state, _ = self.state(name)
        if state != "approval":
            raise BridgeError("session %r is %s, not approval; refusing" % (name, state), state_exit(state) or EXIT_ERROR)
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
            except HerdrError as e:
                if e.herdr_code not in ("agent_not_running", "agent_not_found", "agent_blocked",
                                        "agent_prompt_stalled", "timeout"):
                    raise
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
            label = tab.get("label")
            if not label or not NAME_RE.match(label):
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
            if not name or not NAME_RE.match(name):
                continue
            a = agents.get(name)
            if a:
                st = classify(a.get("agent_status"), self.explain_rule(name) if a.get("agent_status") == "blocked" else None)
                sid = (a.get("agent_session") or {}).get("value") or self.store.load(name).get("agent_session_id")
                rows.append({"name": name, "pane_id": a.get("pane_id"), "state": st, "session_id": sid})
            else:
                rows.append({"name": name, "pane_id": None, "state": "dead", "session_id": self.store.load(name).get("agent_session_id")})
        return rows
