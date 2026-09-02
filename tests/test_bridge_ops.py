import atexit, os, shutil, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import herdrbridge as hb
from fakes import FakeHerdr, agent, ok, WS

CFG = hb.BridgeConfig(workspace_label="hermes-bridge", kind="hermes", default_cwd="/Users/fabzter",
                      shell_settle_s=0.05, poll_s=0)


def _tmpdir():
    """A mkdtemp() that cleans up at interpreter exit instead of leaking (helper used by `bridge()`,
    a free function shared by many test methods with no TestCase to hang addCleanup off of)."""
    d = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d


def bridge(h):
    return hb.Bridge(h, CFG, hb.StateStore(_tmpdir()))


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

    def test_restorable_requires_stored_pane_to_have_no_agent(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[])],
                       "pane get": [ok("pane_info", pane={"pane_id": "w1:p3", "workspace_id": "w1", "agent": "hermes"})]})
        b = bridge(h); b.store.save("bean", pane_id="w1:p3")
        self.assertEqual(b.resolve("bean"), ("missing", None))

    def test_resolve_rejects_stored_pane_in_different_workspace(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[])],
                       "pane get": [ok("pane_info", pane={"pane_id": "w1:p3", "workspace_id": "w9"})]})
        b = bridge(h); b.store.save("bean", pane_id="w1:p3")
        self.assertEqual(b.resolve("bean"), ("missing", None))


class PaneIsShellTests(unittest.TestCase):
    def test_empty_foreground_processes_is_not_shell(self):
        h = FakeHerdr({"pane process-info": [ok("pane_process_info", process_info={"foreground_processes": []})]})
        self.assertFalse(bridge(h).pane_is_shell("w1:p3"))

    def test_non_shell_foreground_process_is_not_shell(self):
        h = FakeHerdr({"pane process-info": [ok("pane_process_info", process_info={"foreground_processes": [{"name": "node"}]})]})
        self.assertFalse(bridge(h).pane_is_shell("w1:p3"))

    def test_pane_not_found_is_not_shell(self):
        h = FakeHerdr({"pane process-info": [hb.HerdrError("pane_not_found", "gone")]})
        self.assertFalse(bridge(h).pane_is_shell("w1:p3"))

    def test_not_found_is_not_shell(self):
        h = FakeHerdr({"pane process-info": [hb.HerdrError("not_found", "gone")]})
        self.assertFalse(bridge(h).pane_is_shell("w1:p3"))


class FindAgentTests(unittest.TestCase):
    def test_refuses_ambiguous_same_name_agents(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean", pane="w1:p1"), agent("bean", pane="w1:p9")])]})
        with self.assertRaises(hb.BridgeError) as cm:
            bridge(h).find_agent("bean")
        self.assertEqual(cm.exception.code, hb.EXIT_ERROR)


class RecordSessionTests(unittest.TestCase):
    def test_nothing_to_record_creates_no_state_file(self):
        h = FakeHerdr({})
        b = bridge(h)
        b.record_session("x", {})
        self.assertFalse(os.path.exists(os.path.join(b.store.dir, "x.json")))
        self.assertEqual(b.store.load("x"), {})


READY_SHELL = ok("pane_process_info", process_info={"foreground_processes": [{"name": "zsh", "argv": ["-zsh"]}]})
PANE_EXISTS = ok("pane_info", pane={"pane_id": "w1:p5", "workspace_id": "w1"})


class StartTests(unittest.TestCase):
    def test_start_missing_creates_tab_and_resumes_stored_session(self):
        started = agent("bean", pane="w1:p5", tab="w1:t3", session="S1")
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w1:t3", "label": "bean"}, root_pane={"pane_id": "w1:p5"})],
                       "pane get": [PANE_EXISTS],
                       "pane process-info": [READY_SHELL],
                       "agent start": [ok("agent_started", agent=started, argv=["hermes", "chat"])]})
        b = bridge(h); b.store.save("bean", agent_session_id="S1")
        a = b.start("bean", ["chat", "--cli", "--source", "tool"])
        self.assertEqual(a["pane_id"], "w1:p5")
        start = [c for c in h.calls if c[:3] == ("cli", "agent", "start")][0]
        self.assertEqual(start[3:], ("bean", "--kind", "hermes", "--pane", "w1:p5", "--timeout", "120000", "--",
                                     "chat", "--cli", "--source", "tool", "--resume", "S1"))
        self.assertEqual(b.store.load("bean")["pane_id"], "w1:p5")
        self.assertEqual(b.store.load("bean")["agent_session_id"], "S1")

    def test_start_fresh_drops_resume(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w1:t3"}, root_pane={"pane_id": "w1:p5"})],
                       "pane get": [PANE_EXISTS],
                       "pane process-info": [READY_SHELL],
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

    def test_start_agent_not_ready_keeps_created_tab_and_pane_in_store(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w1:t3"}, root_pane={"pane_id": "w1:p5"})],
                       "pane get": [PANE_EXISTS],
                       "pane process-info": [READY_SHELL],
                       "agent start": [hb.HerdrError("agent_not_ready", "still booting")]})
        b = bridge(h)
        a = b.start("bean", ["chat"])
        self.assertEqual(a["agent_status"], "blocked")
        st = b.store.load("bean")
        self.assertEqual(st["tab_id"], "w1:t3")
        self.assertEqual(st["pane_id"], "w1:p5")

    def test_start_waits_for_a_freshly_created_pane_to_stop_being_busy(self):
        # A just-created pane's shell can still be mid-startup (e.g. a slow zsh/pyenv rehash),
        # so its foreground process isn't a plain shell yet; `agent start` would fail
        # immediately with agent_pane_busy if fired right away. start() must poll
        # pane_is_shell() until it settles before ever calling `agent start`.
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w1:t3"}, root_pane={"pane_id": "w1:p5"})],
                       "pane get": [PANE_EXISTS],
                       "pane process-info": [
                           ok("pane_process_info", process_info={"foreground_processes": [
                               {"name": "sleep", "argv": ["sleep", "0.1"]},
                               {"name": "bash", "argv": ["bash", "pyenv-rehash"]}]}),
                           READY_SHELL,
                       ],
                       "agent start": [ok("agent_started", agent=agent("bean", pane="w1:p5"))]})
        b = bridge(h)
        b.start("bean", ["chat"])
        pinfo_idx = [i for i, c in enumerate(h.calls) if c[:3] == ("cli", "pane", "process-info")]
        start_idx = next(i for i, c in enumerate(h.calls) if c[:3] == ("cli", "agent", "start"))
        self.assertGreaterEqual(len(pinfo_idx), 2)
        self.assertGreater(start_idx, pinfo_idx[-1])  # agent start only fired after the pane settled

    def test_start_retries_agent_start_itself_on_a_late_agent_pane_busy(self):
        # pane_is_shell() is only a snapshot taken before the call; herdr's own busy check at
        # the moment `agent start` actually fires is authoritative and can still lose a brief
        # race even right after pane_is_shell() reported ready. start() must retry the `agent
        # start` call itself (not just its own pre-check) when herdr reports agent_pane_busy.
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w1:t3"}, root_pane={"pane_id": "w1:p5"})],
                       "pane get": [PANE_EXISTS],
                       "pane process-info": [READY_SHELL],
                       "agent start": [hb.HerdrError("agent_pane_busy", "not an available shell"),
                                       ok("agent_started", agent=agent("bean", pane="w1:p5"))]})
        b = bridge(h)
        a = b.start("bean", ["chat"])
        self.assertEqual(a["pane_id"], "w1:p5")
        start_calls = [c for c in h.calls if c[:3] == ("cli", "agent", "start")]
        self.assertEqual(len(start_calls), 2)

    def test_start_gives_up_and_raises_after_agent_pane_busy_persists(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w1:t3"}, root_pane={"pane_id": "w1:p5"})],
                       "pane get": [PANE_EXISTS],
                       "pane process-info": [READY_SHELL],
                       "agent start": [hb.HerdrError("agent_pane_busy", "not an available shell")]})
        b = bridge(h)
        with self.assertRaises(hb.HerdrError) as cm:
            b.start("bean", ["chat"], busy_wait_s=0)
        self.assertEqual(cm.exception.herdr_code, "agent_pane_busy")
        self.assertEqual(cm.exception.code, hb.EXIT_BUSY)

    def test_start_pane_vanished_before_settle_returns_immediately_without_polling(self):
        # If the freshly created pane is already gone by the time we check it (e.g. herdr
        # closed it racing this call), _await_shell_ready must return immediately instead of
        # burning the whole shell_settle_s window polling pane_is_shell() on a pane that no
        # longer exists. No "pane process-info" result is scripted below: if pane_is_shell()
        # were called at all, FakeHerdr would raise for the missing scripted result.
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[])],
                       "tab create": [ok("tab_created", tab={"tab_id": "w1:t3"}, root_pane={"pane_id": "w1:p5"})],
                       "pane get": [hb.HerdrError("pane_not_found", "gone")],
                       "agent start": [ok("agent_started", agent=agent("bean", pane="w1:p5"))]})
        b = bridge(h)
        b.start("bean", ["chat"])
        self.assertFalse([c for c in h.calls if c[:3] == ("cli", "pane", "process-info")])


class ExplainRuleTests(unittest.TestCase):
    def test_returns_none_when_explain_raises(self):
        h = FakeHerdr({"agent explain": [hb.HerdrError("not_found", "no such agent")]})
        self.assertIsNone(bridge(h).explain_rule("bean"))

    def test_finds_rule_top_level(self):
        h = FakeHerdr({"agent explain": [{"matched_rule": {"id": "credential_prompt"}}]})
        self.assertEqual(bridge(h).explain_rule("bean"), "credential_prompt")

    def test_finds_rule_nested_under_result_explain(self):
        h = FakeHerdr({"agent explain": [
            {"result": {"type": "agent_explain", "explain": {"matched_rule": {"id": "credential_prompt"}}}}]})
        self.assertEqual(bridge(h).explain_rule("bean"), "credential_prompt")


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

    def test_send_agent_blocked_during_prompt_returns_blocked_state_and_dialog(self):
        blocked = agent("bean", status="blocked")
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean")]), ok("agent_list", agents=[blocked])],
                       "agent prompt": [hb.HerdrError("agent_blocked", "credential required")],
                       "agent explain": [{"matched_rule": {"id": "credential_prompt"}}]},
                      {"agent read": ["", "some blocked screen\n"]})
        state, reply, trunc, dialog = bridge(h).send("bean", "hello", 1000)
        self.assertEqual(state, "secret")
        self.assertEqual(reply, "")
        self.assertTrue(dialog.startswith("MESSAGE NOT DELIVERED"))
        self.assertFalse([c for c in h.calls if c[:3] == ("cli", "agent", "wait")])
        # the "after" read is discarded on this path (extract_reply never runs); it must not be performed
        before_reads = [c for c in h.calls if c[:5] == ("text", "agent", "read", "bean", "--source") and c[5] == "recent-unwrapped"]
        self.assertEqual(len(before_reads), 1)


class AnswerTests(unittest.TestCase):
    def test_refuses_when_not_clarify(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean", status="idle")])]})
        with self.assertRaises(hb.BridgeError) as cm:
            bridge(h).answer("bean", "42", settle_s=0)
        self.assertEqual(cm.exception.code, 1)  # refusal exit codes must never be 0, even for idle

    def test_sends_text_then_enter_and_returns_new_state(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean", status="blocked", pane="w1:p1")]),
                                      ok("agent_list", agents=[agent("bean", status="working", pane="w1:p1")])],
                       "agent explain": [{"matched_rule": {"id": "clarification_prompt"}}],
                       "pane send-text": [ok("pane_send_text")],
                       "pane send-keys": [ok("pane_send_keys")]})
        b = bridge(h)
        result = b.answer("bean", "42", settle_s=0)
        self.assertEqual(result, "busy")
        self.assertEqual([c[3:] for c in h.calls if c[:3] == ("cli", "pane", "send-text")], [("w1:p1", "42")])
        self.assertEqual([c[3:] for c in h.calls if c[:3] == ("cli", "pane", "send-keys")], [("w1:p1", "enter")])

    def test_raises_when_still_clarify_after_answering(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean", status="blocked", pane="w1:p1")])],
                       "agent explain": [{"matched_rule": {"id": "clarification_prompt"}}],
                       "pane send-text": [ok("pane_send_text")],
                       "pane send-keys": [ok("pane_send_keys")]})
        b = bridge(h)
        with self.assertRaises(hb.BridgeError) as cm:
            b.answer("bean", "42", settle_s=0)
        self.assertEqual(cm.exception.code, hb.EXIT_CLARIFY)


class MenuNavTests(unittest.TestCase):
    def test_navigate_to_deny_sends_down_then_enter(self):
        footer = "↑/↓ to select · Enter confirm\n"
        menu1 = "▸ 1. Allow once\n  2. Deny\n" + footer
        menu2 = "  1. Allow once\n▸ 2. Deny\n" + footer
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean", status="blocked")]), ok("agent_list", agents=[agent("bean", status="working")])],
                       "agent explain": [{"matched_rule": {"id": "dangerous_command_approval"}}],
                       "agent send-keys": [ok("agent_send_keys")]},
                      {"agent read": [menu1, menu2, menu2]})
        b = bridge(h)
        self.assertEqual(b.navigate_menu("bean", "Deny", settle_s=0), "busy")
        keys = [c[4] for c in h.calls if c[:3] == ("cli", "agent", "send-keys")]
        self.assertEqual(keys, ["down", "enter"])

    def test_navigate_refuses_when_idle_with_nonzero_exit_code(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean", status="idle")])]})
        with self.assertRaises(hb.BridgeError) as cm:
            bridge(h).navigate_menu("bean", "Deny", settle_s=0)
        self.assertEqual(cm.exception.code, 1)  # refusal exit codes must never be 0, even for idle

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

    def test_stop_waits_for_agent_list_to_go_empty(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean", tab="w1:t2")]),
                                      ok("agent_list", agents=[agent("bean", tab="w1:t2")]),
                                      ok("agent_list", agents=[])],
                       "agent prompt": [ok("agent_prompt", agent=agent("bean"))],
                       "tab close": [ok("tab_closed")]})
        b = bridge(h)
        self.assertTrue(b.stop("bean", wait_s=2))
        prompt_idx = next(i for i, c in enumerate(h.calls) if c[:3] == ("cli", "agent", "prompt"))
        calls_after_prompt = [c for c in h.calls[prompt_idx + 1:] if c[:3] == ("cli", "agent", "list")]
        self.assertGreaterEqual(len(calls_after_prompt), 2)

    def test_stop_tolerates_agent_prompt_stalled(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean", tab="w1:t2")]), ok("agent_list", agents=[])],
                       "agent prompt": [hb.HerdrError("agent_prompt_stalled", "no change")],
                       "tab close": [ok("tab_closed")]})
        b = bridge(h)
        self.assertTrue(b.stop("bean", wait_s=0))
        self.assertEqual([c for c in h.calls if c[:3] == ("cli", "tab", "close")][0][3], "w1:t2")

    def test_stop_tolerates_tab_close_not_found(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[agent("bean", tab="w1:t2")]), ok("agent_list", agents=[])],
                       "agent prompt": [ok("agent_prompt", agent=agent("bean"))],
                       "tab close": [hb.HerdrError("not_found", "already closed")]})
        b = bridge(h)
        self.assertTrue(b.stop("bean", wait_s=0))

    def test_gc_closes_shell_only_tabs(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[])],
                       "tab list": [ok("tab_list", tabs=[{"tab_id": "w1:t1", "label": "bean"}, {"tab_id": "w1:t2", "label": "old"},
                                                          {"tab_id": "w1:t3", "label": "1"}, {"tab_id": "w1:t4", "label": "scratch pane"},
                                                          {"tab_id": "w1:t5", "label": None}])],
                       "pane list": [ok("pane_list", panes=[{"pane_id": "w1:p1", "tab_id": "w1:t1", "agent": "hermes", "agent_status": "idle"},
                                                             {"pane_id": "w1:p2", "tab_id": "w1:t2", "agent_status": "unknown"},
                                                             {"pane_id": "w1:p3", "tab_id": "w1:t3", "agent_status": "unknown"},
                                                             {"pane_id": "w1:p4", "tab_id": "w1:t4", "agent_status": "unknown"},
                                                             {"pane_id": "w1:p5", "tab_id": "w1:t5", "agent_status": "unknown"}])],
                       "pane process-info": [ok("pane_process_info", process_info={"foreground_processes": [{"name": "zsh"}]})],
                       "tab close": [ok("tab_closed")]})
        self.assertEqual(bridge(h).gc(), ["w1:t2"])

    def test_gc_skips_tab_whose_pane_process_info_raises_pane_not_found(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "agent list": [ok("agent_list", agents=[])],
                       "tab list": [ok("tab_list", tabs=[{"tab_id": "w1:t2", "label": "old"}])],
                       "pane list": [ok("pane_list", panes=[{"pane_id": "w1:p2", "tab_id": "w1:t2", "agent_status": "unknown"}])],
                       "pane process-info": [hb.HerdrError("pane_not_found", "gone")]})
        self.assertEqual(bridge(h).gc(), [])

    def test_list_sessions(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "tab list": [ok("tab_list", tabs=[{"tab_id": "w1:t1", "label": "bean"}])],
                       "agent list": [ok("agent_list", agents=[agent("bean", session="S1")])]})
        rows = bridge(h).list_sessions()
        self.assertEqual(rows, [{"name": "bean", "pane_id": "w1:p1", "state": "idle", "session_id": "S1"}])

    def test_list_sessions_skips_tabs_with_invalid_labels(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "tab list": [ok("tab_list", tabs=[{"tab_id": "w1:t1", "label": "bean"},
                                                          {"tab_id": "w1:t2", "label": "../../evil"},
                                                          {"tab_id": "w1:t3", "label": None}])],
                       "agent list": [ok("agent_list", agents=[agent("bean", session="S1")])]})
        rows = bridge(h).list_sessions()
        self.assertEqual(rows, [{"name": "bean", "pane_id": "w1:p1", "state": "idle", "session_id": "S1"}])

    def test_list_sessions_dead_when_tab_has_no_agent(self):
        h = FakeHerdr({"workspace list": [ok("workspace_list", workspaces=[WS])],
                       "tab list": [ok("tab_list", tabs=[{"tab_id": "w1:t1", "label": "ghost"}])],
                       "agent list": [ok("agent_list", agents=[])]})
        rows = bridge(h).list_sessions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], "dead")
        self.assertIsNone(rows[0]["pane_id"])


if __name__ == "__main__":
    unittest.main()
