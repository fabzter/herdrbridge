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
