from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contextcast.store import Store


class StoreWorkflowTests(unittest.TestCase):
    def test_saved_events_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "contextcast.db")
            first_event = store.list_events()[0]
            store.add_interaction("demo", first_event.id, "save")

            saved = store.saved_events("demo")
            report = store.portfolio_report("demo")

            self.assertEqual(saved[0].id, first_event.id)
            self.assertIn("ContextCast Portfolio Report", report)
            self.assertIn("CV Bullets", report)


if __name__ == "__main__":
    unittest.main()
