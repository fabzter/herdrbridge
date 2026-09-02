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
