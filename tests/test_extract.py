import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import herdrbridge as hb
FIX = os.path.join(os.path.dirname(__file__), "fixtures")
def fx(n):
    with open(os.path.join(FIX, n), encoding="utf-8") as f: return f.read()


class HermesExtractTests(unittest.TestCase):
    def test_reply_from_hermes_box(self):
        reply, trunc = hb.extract_reply(fx("hermes_before.txt"), fx("hermes_reply.txt"),
                                        "Reply with exactly the word OK and nothing else.", "hermes")
        self.assertEqual(reply, "OK"); self.assertFalse(trunc)

    def test_multiline_prompt_anchors_on_first_line(self):
        reply, trunc = hb.extract_reply("", fx("hermes_reply.txt"),
                                        "Reply with exactly the word OK and nothing else.\nsecond line ignored", "hermes")
        self.assertEqual(reply, "OK"); self.assertFalse(trunc)

    def test_missing_anchor_falls_back_to_new_text(self):
        before = fx("hermes_before.txt"); after = fx("hermes_reply.txt")
        reply, trunc = hb.extract_reply(before, after, "totally different prompt", "hermes")
        self.assertTrue(trunc)
        self.assertIn("OK", reply)
        self.assertNotIn("Welcome to Hermes Agent", reply)  # before-text removed

    def test_missing_anchor_and_no_overlap_returns_tail(self):
        reply, trunc = hb.extract_reply("unrelated\nold\n", fx("hermes_reply.txt"), "nope", "hermes")
        self.assertTrue(trunc); self.assertIn("OK", reply)

    def test_box_with_bar_prefixed_lines_is_unwrapped(self):
        after = "● hi\n╭─ ⚕ Hermes  10:00─╮\n│ line one   │\n│ line two   │\n╰──────╯\n❯\n"
        self.assertEqual(hb.extract_reply("", after, "hi", "hermes"), ("line one\nline two", False))

    def test_short_prompt_does_not_false_anchor(self):
        after = "● okay here is something unrelated\n╭─ ⚕ Hermes  10:00─╮\nwrong\n╰──╯\n● ok\n╭─ ⚕ Hermes  10:01─╮\nright\n╰──╯\n❯\n"
        reply, trunc = hb.extract_reply("", after, "ok", "hermes")
        self.assertEqual(reply, "right"); self.assertFalse(trunc)

    def test_short_prompt_with_only_prefix_echo_falls_back(self):
        after = "● okay here is something unrelated\n╭─ ⚕ Hermes  10:00─╮\nwrong\n╰──╯\n❯\n"
        reply, trunc = hb.extract_reply("", after, "ok", "hermes")
        self.assertTrue(trunc)  # Fallback used since no exact match for "ok"

    def test_echo_found_without_hermes_box_falls_back_and_is_truncated(self):
        after = "● hi\nno box here\n❯\n"
        reply, trunc = hb.extract_reply("", after, "hi", "hermes")
        self.assertEqual(reply, "no box here")
        self.assertTrue(trunc)

    def test_new_text_match_with_nothing_new_returns_empty(self):
        before = fx("hermes_before.txt")
        after = fx("hermes_before.txt")
        reply, trunc = hb.extract_reply(before, after, "nope", "hermes")
        self.assertEqual(reply, ""); self.assertTrue(trunc)


class ClaudeExtractTests(unittest.TestCase):
    # claude_reply.txt is a live capture (claude-bridge tests/live/e2e_claude.sh against real
    # Claude Code 2.1.236) of `read e2e -n 120` after asking the prompt below in a fresh,
    # read-only session opened on this repo. The extracted reply leads with the wrapped second
    # line of the (single-line) submitted prompt: extract_reply() anchors on the first physical
    # terminal line of the echo ('❯ <prompt start>'), so a prompt long enough to wrap leaves its
    # continuation line as the start of the body — a known, minor cosmetic quirk, not something
    # this test is trying to hide.
    def test_reply_after_echo_without_ui_chrome(self):
        prompt = ("Read README.md in the current directory and answer in one sentence: "
                  "what is this repo? Reply with only that sentence.")
        reply, trunc = hb.extract_reply("", fx("claude_reply.txt"), prompt, "claude")
        self.assertFalse(trunc)
        self.assertTrue(reply.startswith("Reply with only that sentence."))
        self.assertIn("A Hermes Agent skill that lets Hermes hold a continuing, read-only "
                      "conversation with Claude", reply)
        self.assertNotIn("❯", reply); self.assertNotIn("? for shortcuts", reply)
        self.assertNotIn("Claude Code v", reply)
        self.assertNotIn("ctrl+o to expand", reply); self.assertNotIn("Cogitated", reply)

    def test_real_claude_code_echoes_prompt_with_fancy_angle_not_ascii_gt(self):
        # Claude Code >= 2.1 echoes the submitted prompt with '❯' (U+276F), not the ASCII
        # '>' the synthetic fixture used. Also strips the collapsed tool-summary line
        # ("Read 1 file (ctrl+o to expand)") and the elapsed-time spinner footer
        # ("✻ Sautéed for 4s") as UI chrome, the same way it already strips shortcuts hints.
        after = (
            "❯ Summarize what the file README.md is about in one sentence.\n\n"
            "  Read 1 file (ctrl+o to expand)\n\n"
            "⏺ README.md describes a Hermes Agent skill that lets Hermes hold a continuing,\n"
            "  read-only conversation with Claude Code.\n\n"
            "✻ Sautéed for 4s\n\n"
            "──────\n"
            "❯\n"
            "──────\n"
            "  ⏵⏵ auto mode on (shift+tab to cycle)\n"
        )
        reply, trunc = hb.extract_reply("", after,
                                        "Summarize what the file README.md is about in one sentence.", "claude")
        self.assertFalse(trunc)
        self.assertEqual(reply, "README.md describes a Hermes Agent skill that lets Hermes hold a continuing,\n"
                                 "  read-only conversation with Claude Code.")
        self.assertNotIn("ctrl+o to expand", reply)
        self.assertNotIn("Sautéed", reply)


if __name__ == "__main__":
    unittest.main()
