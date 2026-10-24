"""The pinned mCRL2 toolset and the two commands that linearise (docs/spec.md §3.2).

    mcrl22lps --lin-method=regular --no-constelm -n <input>.mcrl2 <input>.lps
    lpspp --format=internal <input>.lps <input>.aterm

The toolset is found on PATH. When it is missing or its version is not the pin,
the generator refuses (G1); it never passes by skipping (§3.15.4).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .errors import Diagnostic, GeneratorError

LINEARISE = ["mcrl22lps", "--lin-method=regular", "--no-constelm", "-n"]
PRETTY = ["lpspp", "--format=internal"]
_VERSION = re.compile(r"mCRL2 toolset (\S+)")


class Toolchain:
    """The mCRL2 tools the generator runs."""

    def version(self) -> str | None:
        """The toolset version `mcrl22lps --version` reports, or None when it is not installed."""
        exe = shutil.which("mcrl22lps")
        if exe is None:
            return None
        p = subprocess.run([exe, "--version"], capture_output=True, text=True)
        m = _VERSION.search(p.stdout + p.stderr)
        return m.group(1) if m else None

    def linearise(self, text: str, name: str) -> str:
        """The ATerm text of the LPS of `text` (§3.2.2); G2 when a tool fails."""
        with tempfile.TemporaryDirectory(prefix="naima-codegen-") as d:
            src, lps, out = Path(d, "input.mcrl2"), Path(d, "input.lps"), Path(d, "input.aterm")
            src.write_text(text, encoding="utf-8")
            for cmd in (LINEARISE + [str(src), str(lps)], PRETTY + [str(lps), str(out)]):
                p = subprocess.run(cmd, capture_output=True, text=True)
                if p.returncode != 0:
                    msg = (p.stderr or p.stdout).strip().replace(d + "/", "")
                    raise GeneratorError([Diagnostic("G2", name, cmd[0], "§3.2.2", "%s failed: %s" % (cmd[0], msg))])
            return out.read_text(encoding="utf-8")


def check_version(tools: Toolchain, pin: str, file: str) -> None:
    """G1 unless the installed toolset is the pinned one (§3.2.1)."""
    v = tools.version()
    if v is None:
        raise GeneratorError([Diagnostic("G1", file, "mcrl2_version", "§3.2.1, §3.15.4",
                                         "the mCRL2 toolset (mcrl22lps) is not installed; the pin is %s" % pin)])
    if v != pin:
        raise GeneratorError([Diagnostic("G1", file, "mcrl2_version", "§3.2.1",
                                         "mcrl22lps reports version %s, the pin is %s" % (v, pin))])
