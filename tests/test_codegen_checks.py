"""check-refinement's trace inclusion (docs/spec.md §3.9.2) on a small lock whose grant
lacks the free-lock guard (tests/fixtures/lockbug).

The model itself breaks mutual exclusion, so an observer fires; the refinement is
correct. Trace inclusion must hold (the observers are hidden as tau and the preorder is
the weak one), and only the line "observers" must fail.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "lockbug"
sys.path.insert(0, str(ROOT))

from naima_codegen import cli  # noqa: E402
from naima_codegen.toolchain import Toolchain  # noqa: E402

PIN = "202607.0"


class WeakTraceInclusion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if Toolchain().version() != PIN:
            raise unittest.SkipTest("the pinned mCRL2 toolset %s is not installed" % PIN)
        cls.root = Path(tempfile.mkdtemp(prefix="naima-codegen-lockbug-"))
        shutil.copytree(FIXTURE / "model", cls.root / "model")
        shutil.copyfile(FIXTURE / "codegen.toml", cls.root / "codegen.toml")
        assert cli.main(["generate", "--project", str(cls.root)]) == 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_a_fired_observer_does_not_fail_trace_inclusion(self):
        from naima_codegen.checks import check
        from naima_codegen.project import load

        lines = []
        with tempfile.TemporaryDirectory() as d:
            ok = check(load(self.root), "small", Path(d), lines.append, True)
        text = "\n".join(lines)
        self.assertFalse(ok, text)
        self.assertIn("observers on the refined system: FIRED", text)
        self.assertIn("§3.9.2 trace inclusion: holds", text)


if __name__ == "__main__":
    unittest.main()
