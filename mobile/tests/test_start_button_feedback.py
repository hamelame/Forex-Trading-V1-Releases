"""PAPER Start button must explain unavailable LIVE data on mobile.

Safety invariant: explanatory click path never bypasses the server-side
LIVE quote readiness gate or changes IG broker execution.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class StartButtonFeedbackTests(unittest.TestCase):
    def test_user_can_tap_start_to_learn_why_it_is_blocked(self):
        html = (ROOT / "mobile/web/index.html").read_text("utf-8")
        js = (ROOT / "mobile/web/app.js").read_text("utf-8")
        self.assertIn('id="start-hint"', html)
        self.assertIn('id="start"', html)
        self.assertIn("Start AI is waiting:", js)
        self.assertIn("Start AI blocked:", js)
        self.assertIn("$('start').disabled=!!s.running;", js)
        self.assertIn("if(!ready?.ready)", js)
        self.assertIn("if(app.state?.state_stale)", js)

    def test_backend_still_blocks_paper_start_without_live_data(self):
        code = (ROOT / "mobile/server.py").read_text("utf-8")
        self.assertIn('if not readiness["ready"]:', code)
        self.assertIn('"PAPER start blocked: "', code)
        self.assertIn("if self.persistence_error:", code)

    def test_no_ig_order_controls_changed_for_paper_start_explanation(self):
        js = (ROOT / "mobile/web/app.js").read_text("utf-8")
        self.assertIn("IG DEMO uses a separate Start control", js)
        self.assertIn("No orders are sent.", js)


if __name__ == "__main__":
    unittest.main()
