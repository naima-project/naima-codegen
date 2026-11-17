"""The Lean 4 back end (naima_codegen/leanback.py).

- the property forms it translates, and the names it escapes;
- on examples/jobqueue: the refined Core with its observers becomes a Lean
  module with one Step constructor per summand of its linear process, and with
  lean on PATH (or NAIMA_LEAN naming it) that module elaborates. The end-to-end
  tests need mcrl22lps, and skip, saying so, without it.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "jobqueue"
sys.path.insert(0, str(ROOT))

from naima_codegen import leanback  # noqa: E402


class Forms(unittest.TestCase):
    def test_box_never(self):
        self.assertEqual(leanback.box_never("% a comment\n[true* . o_cache] false\n"), "o_cache")
        self.assertEqual(leanback.box_never("[ true*.o_x ]false"), "o_x")
        self.assertIsNone(leanback.box_never("[true* . a . b] false"))
        self.assertIsNone(leanback.box_never("<true* . a> true"))
        self.assertIsNone(leanback.box_never("[true* . a(1)] false"))

    def test_ident(self):
        self.assertEqual(leanback.ident("kept"), "kept")
        self.assertEqual(leanback.ident("at"), "«at»")
        self.assertEqual(leanback.ident("fun"), "«fun»")


@unittest.skipIf(shutil.which("mcrl22lps") is None, "needs mcrl22lps")
class Jobqueue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from naima_codegen.project import load

        cls.files = leanback.build(load(EXAMPLE), namespace="Jobqueue")

    def test_module(self):
        text = self.files["Jobqueue.lean"]
        self.assertIn("inductive Step (D : Data) : State → Act → State → Prop where", text)
        self.assertIn("structure Laws (D : Data) : Prop where", text)
        self.assertIn("def initial", text)
        self.assertNotIn("sorry", text)
        self.assertEqual(self.files["Mcrl2.lean"], leanback.PRELUDE)

    def test_elaborates(self):
        lean = os.environ.get("NAIMA_LEAN") or shutil.which("lean")
        if lean is None:
            self.skipTest("lean is not installed (naima tools install lean; NAIMA_LEAN=<its lean>)")
        with tempfile.TemporaryDirectory(prefix="naima-lean-") as d:
            for name, text in self.files.items():
                Path(d, name).write_text(text, encoding="utf-8")
            env = dict(os.environ, LEAN_PATH=d)
            p = subprocess.run([lean, "-o", str(Path(d, "Mcrl2.olean")), "Mcrl2.lean"], cwd=d, capture_output=True, text=True, env=env)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            p = subprocess.run([lean, "Jobqueue.lean"], cwd=d, capture_output=True, text=True, env=env)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
