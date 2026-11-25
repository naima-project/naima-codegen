"""Line endings (docs/spec.md §3.15): `generate --check` is neutral to them. A checkout that converts line endings (Windows,
core.autocrlf) holds the model, the generator's own sources and the generated directory with CRLF, and the manifest's hashes
and the comparison still say "up to date". The end-to-end test needs mcrl22lps and skips, saying so, without it.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from naima_codegen import outputs  # noqa: E402

EXAMPLE = ROOT / "examples" / "waits"


def crlf(path: Path) -> None:
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))


class Hashes(unittest.TestCase):
    def test_a_file_hashes_the_same_with_lf_or_crlf(self):
        with tempfile.TemporaryDirectory() as d:
            lf, cr = Path(d) / "lf.txt", Path(d) / "cr.txt"
            lf.write_bytes(b"one\ntwo\n")
            cr.write_bytes(b"one\r\ntwo\r\n")
            self.assertEqual(outputs._sha_text(lf), outputs._sha_text(cr))
            self.assertNotEqual(outputs._sha(lf.read_bytes()), outputs._sha(cr.read_bytes()))


@unittest.skipUnless(shutil.which("mcrl22lps"), "mcrl22lps is not installed")
class Check(unittest.TestCase):
    def run_cg(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "naima_codegen", *args], capture_output=True, text=True, cwd=ROOT)

    def test_check_holds_when_every_file_has_crlf(self):
        with tempfile.TemporaryDirectory() as d:
            project = Path(d) / "waits"
            shutil.copytree(EXAMPLE, project)
            self.assertEqual(self.run_cg("generate", "--project", str(project)).returncode, 0)
            ok = self.run_cg("generate", "--check", "--project", str(project))
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
            # The model, the table and the generated directory as a Windows checkout with autocrlf holds them.
            for f in project.rglob("*"):
                if f.is_file() and f.suffix in {".mcrl2", ".toml", ".json", ".py", ".mcf"}:
                    crlf(f)
            again = self.run_cg("generate", "--check", "--project", str(project))
            self.assertEqual(again.returncode, 0, again.stdout + again.stderr)


if __name__ == "__main__":
    unittest.main()
