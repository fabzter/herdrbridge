import json, os, shutil, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import herdrbridge as hb


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.store = hb.StateStore(self.dir)

    def test_load_missing_is_empty(self):
        self.assertEqual(self.store.load("bean"), {})

    def test_load_corrupt_json_is_empty(self):
        with open(os.path.join(self.dir, "bean.json"), "w") as f:
            f.write("{not json")
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

    def test_delete_after_migration_does_not_resurrect(self):
        with open(os.path.join(self.dir, "bean.session-id"), "w") as f:
            f.write("20260827_113000_abcdef")
        d = self.store.load("bean")
        self.assertEqual(d["agent_session_id"], "20260827_113000_abcdef")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "bean.session-id")))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "bean.session-id.migrated")))
        self.assertTrue(self.store.delete("bean"))
        self.assertEqual(self.store.load("bean"), {})

    def test_delete_removes_unmigrated_legacy_file(self):
        with open(os.path.join(self.dir, "old.session-id"), "w") as f:
            f.write("20260827_113000_abcdef")
        self.assertTrue(self.store.delete("old"))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "old.session-id")))


if __name__ == "__main__":
    unittest.main()
