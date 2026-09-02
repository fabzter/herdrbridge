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


class ConstantsTests(unittest.TestCase):
    def test_session_name_alias(self):
        self.assertEqual(hb.SESSION_NAME, "agents")


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
        self.assertEqual(hb.herdr_error_exit("server_not_running"), 9)
        self.assertEqual(hb.herdr_error_exit("tab_not_found"), 2)
        self.assertEqual(hb.herdr_error_exit("workspace_not_found"), 2)
        self.assertEqual(hb.herdr_error_exit("agent_pane_busy"), 8)
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
