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

    def test_wrapped_prompt_continuation_two_line_echo_is_skipped(self):
        # A long single-line prompt wraps onto two physical terminal lines when echoed; the
        # second line (no '●' marker) must be recognized as continuation of the prompt, not
        # folded into the reply — and the guard (only skip while something's still left to
        # match) must not eat any of the actual reply that follows.
        prompt = ("Summarize the quarterly report and flag any numbers that look inconsistent "
                  "with last quarter's filing please")
        after = ("● Summarize the quarterly report and flag any numbers that look inconsistent with last\n"
                 "quarter's filing please\n"
                 "╭─ ⚕ Hermes  10:00─╮\n"
                 "Nothing looks off; the numbers line up with last quarter.\n"
                 "╰──╯\n❯\n")
        reply, trunc = hb.extract_reply("", after, prompt, "hermes")
        self.assertFalse(trunc)
        self.assertEqual(reply, "Nothing looks off; the numbers line up with last quarter.")
        self.assertNotIn("quarter's filing please", reply)


class ClaudeExtractTests(unittest.TestCase):
    # claude_reply.txt is a live capture (claude-bridge tests/live/e2e_claude.sh against real
    # Claude Code 2.1.236) of `read e2e -n 120` after asking the prompt below in a fresh,
    # read-only session opened on this repo. The submitted prompt is a single (long) line that
    # wraps onto two physical terminal lines when echoed ('❯ <first line>' / '<continuation>');
    # extract_reply() anchors on the first physical line and then walks forward skipping the
    # wrapped continuation line before it starts extracting the reply.
    def test_reply_after_echo_without_ui_chrome(self):
        prompt = ("Read README.md in the current directory and answer in one sentence: "
                  "what is this repo? Reply with only that sentence.")
        reply, trunc = hb.extract_reply("", fx("claude_reply.txt"), prompt, "claude")
        self.assertFalse(trunc)
        self.assertNotIn("Reply with only that sentence.", reply)  # wrapped echo continuation, not reply
        self.assertIn("A Hermes Agent skill that lets Hermes hold a continuing, read-only "
                      "conversation with Claude", reply)
        self.assertNotIn("❯", reply); self.assertNotIn("? for shortcuts", reply)
        self.assertNotIn("Claude Code v", reply)
        self.assertNotIn("ctrl+o to expand", reply); self.assertNotIn("Cogitated", reply)

    def test_real_claude_code_echoes_prompt_with_fancy_angle_not_ascii_gt(self):
        # Claude Code >= 2.1 echoes the submitted prompt with '❯' (U+276F), not the ASCII
        # '>' the synthetic fixture used. Also strips the trailing "(ctrl+o to expand)" hint
        # from the collapsed tool-summary line (keeping "Read 1 file" itself, unlike UI chrome
        # such as shortcuts hints and the elapsed-time spinner footer, which are dropped).
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
        self.assertIn("Read 1 file", reply)
        self.assertIn("README.md describes a Hermes Agent skill that lets Hermes hold a continuing,\n"
                      "  read-only conversation with Claude Code.", reply)
        self.assertNotIn("ctrl+o to expand", reply)
        self.assertNotIn("Sautéed", reply)

    def test_tool_activity_lines_are_kept_only_ctrl_o_hint_is_stripped(self):
        # Policy pinned by the review: tool-activity lines stay in the reply in both renderings
        # (the collapsed "Read 1 file (ctrl+o to expand)" and the expanded "⏺ Read(...)" /
        # "  ⎿  Read N lines" pair); only the trailing parenthetical hint is stripped, and only
        # when it's exactly that hint at the end of the line — a reply sentence that merely
        # mentions "ctrl+o to expand" stays intact.
        after = (
            "❯ do a thing\n\n"
            "⏺ Read(README.md)\n"
            "  ⎿  Read 41 lines\n\n"
            "  Read 1 file (ctrl+o to expand)\n\n"
            "To expand a collapsed tool block press ctrl+o to expand it.\n\n"
            "❯\n"
        )
        reply, trunc = hb.extract_reply("", after, "do a thing", "claude")
        self.assertFalse(trunc)
        self.assertIn("Read(README.md)", reply)
        self.assertIn("⎿  Read 41 lines", reply)
        self.assertIn("Read 1 file", reply)
        self.assertNotIn("(ctrl+o to expand)", reply)
        self.assertIn("To expand a collapsed tool block press ctrl+o to expand it.", reply)

    def test_spinner_glyph_line_without_elapsed_suffix_is_preserved(self):
        # The spinner-chrome regex only matches lines shaped like "<glyph> ... for Ns"; a normal
        # reply line that happens to start with one of those glyphs but doesn't end that way
        # must not be swept up as chrome.
        after = "❯ note\n\n✳ Just a heads up about something important\n\n❯\n"
        reply, trunc = hb.extract_reply("", after, "note", "claude")
        self.assertFalse(trunc)
        self.assertIn("Just a heads up about something important", reply)


if __name__ == "__main__":
    unittest.main()
