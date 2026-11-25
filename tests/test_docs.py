"""The documentation agrees with the code.

- docs/reference.md lists exactly the keys of codegen.toml and policy.toml that the code reads;
- versions.env, the one place that pins the toolchain, agrees with the files that each tool reads;
- every example has a README and a page under docs/examples/.
"""

import inspect
import re
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from naima_codegen import config, policy  # noqa: E402


def _section(text: str, heading: str) -> str:
    """The text under a heading of docs/reference.md, up to the next heading."""
    m = re.search(r"^#+ %s[^\n]*\n(.*?)(?=^#+ |\Z)" % re.escape(heading), text, re.M | re.S)
    assert m, "no section %r in docs/reference.md" % heading
    return m.group(1)


def _keys(text: str) -> set:
    """The first column of the tables of a section: the backticked key names."""
    return set(re.findall(r"^\| `([^`]+)` \|", text, re.M))


def _env() -> dict:
    return dict(re.findall(r"^([A-Z0-9_]+)=(\S+)$", (ROOT / "versions.env").read_text(), re.M))


class Reference(unittest.TestCase):
    ref = (ROOT / "docs" / "reference.md").read_text()

    def same(self, heading, code_keys):
        self.assertEqual(_keys(_section(self.ref, heading)), set(code_keys), heading)

    def test_codegen_top_level(self):
        self.same("Top level (spec §3.6)", config._TOP)

    def test_parameter(self):
        self.same("`[[parameter]]`", config._PARAM)

    def test_action(self):
        self.same("`[[action]]`", config._ACTION)

    def test_observable(self):
        self.same("`[[observable]]`", config._OBS)

    def test_project(self):
        self.same("`[project]`", config._PROJECT)

    def test_systems(self):
        self.same("`[systems]`", config._SYSTEMS)
        self.same("`[systems.ids.<Sort>]`", config._SYSTEM_IDS)

    def test_allowed_values_are_listed(self):
        for v in config.ROLES + config.REPRS + config.BACKENDS + config.WIDTHS:
            self.assertIn("`%s`" % v, self.ref, v)
        for v in policy.ORDERS:
            self.assertIn("`%s`" % v, self.ref, v)

    def test_policy(self):
        src = inspect.getsource(policy.read_policy)
        top = set(re.search(r'if k not in \(([^)]*)\)', src).group(1).replace('"', "").replace(" ", "").split(","))
        self.same("Top level (spec §3.7.1)", top)
        idx = set()
        for a, b, c in re.findall(r'ix\["(\w+)"\]|ix\.get\("(\w+)"|"(\w+)" in ix', src[src.index("for name, ix"):]):
            idx.add(a or b or c)
        idx |= set(re.search(r'for k in \(([^)]*)\) if k not in ix', src).group(1).replace('"', "").replace(" ", "").split(","))
        self.same("`[index.<name>]`", idx)


class Versions(unittest.TestCase):
    env = _env()

    def test_python(self):
        self.assertEqual((ROOT / ".python-version").read_text().strip(), self.env["PYTHON"])
        self.assertEqual(tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["requires-python"], "==%s.*" % self.env["PYTHON"])

    def test_rust(self):
        t = tomllib.loads((ROOT / "rust-toolchain.toml").read_text())
        self.assertEqual(t["toolchain"]["channel"], self.env["RUST"])

    def test_mcrl2(self):
        for p in sorted((ROOT / "examples").glob("*/codegen.toml")):
            self.assertEqual(tomllib.loads(p.read_text())["mcrl2_version"], self.env["MCRL2"], p.parent.name)
        self.assertIn('PIN = "%s"' % self.env["MCRL2"], (ROOT / "tests" / "test_codegen_loop.py").read_text())

    def test_deno_imports(self):
        ts = "".join(p.read_text() for p in (ROOT / "tests").glob("**/*.ts")) + (ROOT / "tests" / "codegen_ts.py").read_text()
        self.assertIn("svelte@%s" % self.env["SVELTE"], ts)
        self.assertIn("typescript@%s" % self.env["TYPESCRIPT"], ts)
        self.assertNotRegex(ts, r"svelte@(?!%s)" % re.escape(self.env["SVELTE"]))
        self.assertNotRegex(ts, r"typescript@(?!%s)" % re.escape(self.env["TYPESCRIPT"]))


class Pages(unittest.TestCase):
    def test_every_example_is_documented(self):
        for d in sorted((ROOT / "examples").iterdir()):
            if d.is_dir():
                self.assertTrue((d / "README.md").is_file(), "examples/%s/README.md" % d.name)
                self.assertTrue((ROOT / "docs" / "examples" / (d.name + ".md")).is_file(), "docs/examples/%s.md" % d.name)


if __name__ == "__main__":
    unittest.main()
