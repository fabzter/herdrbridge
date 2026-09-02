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

    def test_keep_less_than_one_is_noop(self):
        self.write(self.p, 20)
        self.assertFalse(hb.rotate_log(self.p, max_bytes=10, keep=0))
        self.assertTrue(os.path.exists(self.p))
        self.assertFalse(os.path.exists(self.p + ".1"))

    def test_over_threshold_rotates_and_keeps_n(self):
        self.write(self.p, 20); self.assertTrue(hb.rotate_log(self.p, max_bytes=10, keep=2))
        self.assertFalse(os.path.exists(self.p)); self.assertTrue(os.path.exists(self.p + ".1"))
        self.write(self.p, 20); hb.rotate_log(self.p, max_bytes=10, keep=2)
        self.write(self.p, 20); hb.rotate_log(self.p, max_bytes=10, keep=2)
        self.assertTrue(os.path.exists(self.p + ".1")); self.assertTrue(os.path.exists(self.p + ".2"))
        self.assertFalse(os.path.exists(self.p + ".3"))


if __name__ == "__main__":
    unittest.main()
