"""Isolated CLI checks for roadmap updates and static-site export.

Run from the repository root: python -B -m unittest discover -s tools/roadmap -v
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "work" / "roadmap-pages-20261107"


class RoadmapCliTests(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="cli-", dir=WORK)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.docs = self.root / "docs" / "roadmap"
        self.docs.mkdir(parents=True)
        (self.root / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts" / "roadmap.py", self.root / "scripts" / "roadmap.py")
        self.data_path = self.docs / "phases.json"
        self.report_path = self.docs / "ROADMAP.md"
        self.initial = {
            "groups": {"00": "工程基线", "01": "架构契约"},
            "phases": [
                {"id": "00", "title": "工程基线", "status": "todo"},
                {"id": "01", "title": "整体架构与契约", "status": "todo"},
            ],
            "edges": {"hard": [["00", "01"]], "integration": [], "paid": []},
        }
        self.data_path.write_text(json.dumps(self.initial, ensure_ascii=False) + "\n", encoding="utf-8")
        self.run_cli("render")

    def run_cli(self, *args, success=True):
        env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(
            [sys.executable, "-B", str(self.root / "scripts" / "roadmap.py"), *args],
            cwd=self.root, env=env, capture_output=True, text=True, encoding="utf-8",
            timeout=20,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(result.stderr.strip(), "Invalid updates should explain their failure")
        return result

    def snapshot(self):
        return self.data_path.read_bytes(), self.report_path.read_bytes()

    def load_data(self):
        return json.loads(self.data_path.read_text(encoding="utf-8"))

    def assert_rejected_without_writes(self, *args):
        before = self.snapshot()
        self.run_cli(*args, success=False)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_updates_leave_both_files_unchanged(self):
        cases = [
            ("set", "00", "blocked"),
            ("set", "00", "blocked", "--note", "00", "   "),
            ("set", "00", "doing", "--owner", "00", "   "),
            ("set", "00", "doing", "--pr", "00", "#12"),
            ("set", "00", "doing", "--date", "2026-02-30"),
            ("set", "00", "doing", "--date", "2026-11-7"),
            ("set", "00", "doing", "01", "not-a-status"),
            ("set", "00", "doing", "01", "blocked"),
            ("set", "00", "doing", "--note", "00", "先在内存中修改", "--owner", "99", "Codex"),
        ]
        for args in cases:
            with self.subTest(args=args):
                self.assert_rejected_without_writes(*args)

    def test_history_cannot_be_backdated_before_existing_entries(self):
        self.run_cli("set", "00", "doing", "--date", "2026-11-06")
        self.assert_rejected_without_writes("set", "00", "review", "--date", "2026-11-05")

    def test_blocked_note_cannot_be_cleared_while_still_blocked(self):
        self.run_cli("set", "00", "blocked", "--note", "00", "等待验收", "--date", "2026-11-06")
        self.assert_rejected_without_writes("set", "--note", "00", "")
        self.run_cli("set", "00", "doing", "--note", "00", "", "--date", "2026-11-07")
        self.assertNotIn("note", self.load_data()["phases"][0])
        self.run_cli("check")

    def test_valid_set_appends_history_and_updates_report(self):
        self.run_cli(
            "set", "00", "doing", "--date", "2026-11-06", "--owner", "00", "Codex",
            "--pr", "00", "https://github.com/example/roadmap/pull/12",
        )
        first_report = self.report_path.read_text(encoding="utf-8")
        self.run_cli("set", "00", "review", "--date", "2026-11-07")
        phase = self.load_data()["phases"][0]
        self.assertEqual(phase["status"], "review")
        self.assertEqual(phase["owner"], "Codex")
        self.assertEqual(phase["prs"], ["https://github.com/example/roadmap/pull/12"])
        self.assertEqual(phase["history"], [
            {"date": "2026-11-06", "status": "doing"},
            {"date": "2026-11-07", "status": "review"},
        ])
        report = self.report_path.read_text(encoding="utf-8")
        self.assertNotEqual(report, first_report)
        self.assertIn("| 00 工程基线 | 🟣 待验收 | Codex |", report)
        self.assertIn("[#12](https://github.com/example/roadmap/pull/12)", report)
        self.run_cli("check")
        self.run_cli("set", "00", "review", "--date", "2026-11-07")
        self.assertEqual(self.load_data()["phases"][0]["history"], phase["history"])

    def test_invalid_existing_history_date_fails_check(self):
        data = self.load_data()
        data["phases"][0]["status"] = "doing"
        data["phases"][0]["history"] = [{"date": "2026-02-30", "status": "doing"}]
        self.data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.assert_rejected_without_writes("check")

    def test_existing_blocked_phase_requires_reason(self):
        data = self.load_data()
        data["phases"][0]["status"] = "blocked"
        self.data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.assert_rejected_without_writes("check")

    def test_readonly_site_reexport_updates_json_and_embedded_snapshot(self):
        # Exercise the real template, including its config and local Mermaid dependency.
        shutil.copytree(ROOT / "docs" / "roadmap" / "site", self.docs / "site")
        target = self.root / "public" / "roadmap"
        # A Git checkout may use LF even on Windows; export must preserve those bytes.
        self.data_path.write_bytes(self.data_path.read_bytes().replace(b"\r\n", b"\n"))

        def assert_export(expected):
            self.run_cli("site", str(target), "--readonly")
            html = (target / "index.html").read_text(encoding="utf-8")
            match = re.search(r'<script id="default-data" type="application/json">(.*?)</script>', html, re.S)
            self.assertIsNotNone(match)
            self.assertEqual(json.loads(match.group(1)), expected)
            self.assertEqual(json.loads((target / "phases.json").read_text(encoding="utf-8")), expected)
            self.assertEqual((target / "phases.json").read_bytes(), self.data_path.read_bytes())
            self.assertIn("editable: false", html)
            self.assertNotIn("editable: true", html)
            self.assertIn("dataUrl: 'phases.json'", html)
            for name in ("mermaid.min.js", "LICENSE-mermaid.txt"):
                self.assertEqual((target / "vendor" / name).read_bytes(), (self.docs / "site" / "vendor" / name).read_bytes())

        assert_export(self.load_data())
        self.run_cli("set", "00", "doing", "--date", "2026-11-07", "--note", "00", "包含 </script> 的备注")
        assert_export(self.load_data())


if __name__ == "__main__":
    unittest.main()
