"""The launchers (naima-codegen, naima-codegen.cmd) start the tool: with NAIMA_PYTHON, and through uv."""

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(env_extra: dict) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("NAIMA_PYTHON", "NAIMA_UV")}
    env.update(env_extra)
    if os.name == "nt":
        cmd = ["cmd.exe", "/c", str(ROOT / "naima-codegen.cmd"), "--version"]
    else:
        cmd = [str(ROOT / "naima-codegen"), "--version"]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)


class Launcher(unittest.TestCase):
    def test_naima_python_override(self):
        r = run({"NAIMA_PYTHON": sys.executable})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("naima-codegen "), r.stdout)

    @unittest.skipUnless(shutil.which("uv"), "uv is not installed")
    def test_through_uv_with_the_pinned_python(self):
        r = run({})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("naima-codegen "), r.stdout)
        py = subprocess.run(
            ["uv", "run", "--project", str(ROOT), "--no-dev", "--frozen", "--quiet", "python", "-c", "import sys; print(sys.version_info[:2])"],
            capture_output=True, text=True,
        )
        self.assertEqual(py.stdout.strip(), "(3, 13)")


if __name__ == "__main__":
    unittest.main()
