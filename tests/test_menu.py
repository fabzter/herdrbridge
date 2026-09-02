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

    def test_numbered_lines_in_command_preview_are_ignored(self):
        menu = "Command preview:\n  1. rm -rf /tmp/a\n  2. echo done\n\n" + fx("hermes_approval_menu.txt")
        rows = hb.parse_menu(menu)
        self.assertEqual([(r.number, r.label, r.selected) for r in rows],
                         [(1, "Allow once", True), (2, "Allow for this session", False),
                          (3, "Add to permanent allowlist", False), (4, "Deny", False)])
        self.assertEqual(hb.plan_menu_step(menu, "Deny"), "down")

    def test_refuse_without_footer(self):
        menu = fx("hermes_approval_menu.txt")
        menu_no_footer = "\n".join(menu.splitlines()[:-1])
        self.assertEqual(hb.parse_menu(menu_no_footer), [])
        self.assertIsNone(hb.plan_menu_step(menu_no_footer, "Deny"))

    def test_footer_variants(self):
        menu = fx("hermes_approval_menu.txt").replace("↑/↓ to select · Enter confirm · s show full command", "Enter to confirm")
        self.assertEqual(len(hb.parse_menu(menu)), 4)
        self.assertEqual(hb.plan_menu_step(menu, "Deny"), "down")


if __name__ == "__main__":
    unittest.main()
