"""The code generator's front end reads the linear process and codegen.toml
and refuses what is outside the accepted subset, one code per refusal
(docs/spec.md §3.1–§3.6, §3.14.1), and finds a project's files (§3.6.9).

The LPS fixtures are kept in tests/fixtures/codegen (made by its make.py with
the pinned commands), so most of these tests need no mCRL2. The tests that run
the toolset itself say so when they skip: the pinned toolset is not installed.
"""

import gzip
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "codegen"
EXAMPLE = ROOT / "examples" / "jobqueue"
sys.path.insert(0, str(ROOT))

from naima_codegen import aterm  # noqa: E402
from naima_codegen.errors import CODES, GeneratorError  # noqa: E402
from naima_codegen.expr import show  # noqa: E402
from naima_codegen.frontend import _check_maps, _Report, check, run  # noqa: E402
from naima_codegen.lps import read_lps  # noqa: E402
from naima_codegen.project import load  # noqa: E402
from naima_codegen.render import render_lps, render_refusals  # noqa: E402
from naima_codegen.source import read_catalogue  # noqa: E402
from naima_codegen.toolchain import Toolchain  # noqa: E402

PIN = "202607.0"


def _read(p: Path) -> str:
    return gzip.decompress(p.read_bytes()).decode() if p.suffix == ".gz" else p.read_text()


def _pinned() -> bool:
    return Toolchain().version() == PIN


def _make_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("codegen_fixtures_make", FIX / "make.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _codes(d: Path, aterm_text: str) -> set:
    try:
        check((d / "core.mcrl2").read_text(), (d / "codegen.toml").read_text(), aterm_text, read_catalogue(d / "catalogue"))
    except GeneratorError as e:
        return e.codes
    return set()


class FakeTools:
    """A toolset that reports a version and linearises from a fixture."""

    def __init__(self, version, aterm_text=""):
        self._v, self._a = version, aterm_text

    def version(self):
        return self._v

    def linearise(self, text, name):
        return self._a


def _root_with(core: str, init: str, toml: str) -> str:
    d = tempfile.mkdtemp(prefix="naima-codegen-test-")
    (Path(d) / "model").mkdir()
    (Path(d) / "model" / "core.mcrl2").write_text(core)
    (Path(d) / "model" / "codegen-init.mcrl2").write_text(init)
    (Path(d) / "model" / "codegen.toml").write_text(toml)
    return d


class ATermReader(unittest.TestCase):
    def test_round_trip_is_byte_exact(self):
        for p in (FIX / "mini" / "lps.aterm", FIX / "jobqueue" / "lps.aterm"):
            t = _read(p).strip()
            self.assertEqual(aterm.to_text(aterm.parse(t)), t, p.name)

    def test_quoted_names_empty_arguments_and_integers(self):
        t = aterm.parse('f("[]",[],g(a,),-3,"-")')
        self.assertEqual(t.name, "f")
        self.assertEqual([getattr(x, "name", x) for x in t.args], ["[]", (), "g", -3, "-"])
        self.assertEqual(t.args[2].args[1].name, "")
        self.assertEqual(aterm.to_text(t), 'f("[]",[],g(a,),-3,"-")')

    def test_depth_is_not_bounded_by_the_python_stack(self):
        depth = 200_000
        text = "f(" * depth + "a" + ")" * depth
        t = aterm.parse(text)
        self.assertEqual(aterm.to_text(t), text)
        self.assertEqual(aterm.to_text(aterm.rebuild(t, lambda x: x)), text)

    def test_malformed_text_is_refused(self):
        for bad in ("f(a", "f(a))", "[a,b", "f(a b)"):
            with self.assertRaises(aterm.ATermError, msg=bad):
                aterm.parse(bad)


class LpsReader(unittest.TestCase):
    def setUp(self):
        self.lps = read_lps(_read(FIX / "mini" / "lps.aterm"))

    def test_names_are_recovered(self):
        self.assertEqual([n for n, _ in self.lps.params],
                         ["w", "n", "st", "deps", "RQ", "run", "why", "uses", "tally", "slots"])
        for s in self.lps.summands:
            for n, _ in s.sumvars:
                self.assertNotIn("_Core", n)

    def test_summands_in_source_order_named_by_label(self):
        # -n (§3.2.2): one LPS summand per source summand, none clustered; source order (docs/spec.md §3.5.4).
        self.assertEqual([s.name for s in self.lps.summands],
                         ["c_submit", "c_fire", "c_fire#2", "c_done", "c_finish", "c_finish#2", "c_finish#3", "c_start", "c_halt"])
        self.assertEqual(self.lps.deltas, 1)
        self.assertFalse([s for s in self.lps.structs if s.startswith("Enum")])

    def test_linearisation_does_not_cluster(self):
        from naima_codegen.toolchain import LINEARISE

        self.assertEqual(LINEARISE, ["mcrl22lps", "--lin-method=regular", "--no-constelm", "-n"])
        ex = read_lps(_read(FIX / "jobqueue" / "lps.aterm"))
        self.assertFalse([s for s in ex.structs if s.startswith("Enum")])
        self.assertEqual(len(ex.summands), 6)  # the summands written in the example's core.mcrl2


class Accepted(unittest.TestCase):
    def test_mini_is_accepted_and_matches_its_snapshot(self):
        d = FIX / "mini"
        a = _read(d / "lps.aterm")
        model = check((d / "core.mcrl2").read_text(), (d / "codegen.toml").read_text(), a, [])
        self.assertEqual(render_lps(model.lps) + render_refusals(None), (d / "expected.txt").read_text())
        self.assertEqual(model.summand_kind["c_submit"], "input")
        self.assertEqual(model.summand_kind["c_finish#3"], "output")
        self.assertEqual([p.role for p in model.params],
                         ["config", "scalar", "column", "column", "frontier", "scalar", "scalar", "column", "table", "scalar"])
        self.assertEqual(sorted(m.name for m in model.user_maps), ["anyBusy", "bump", "cap", "isFail", "lim", "next", "tick"])

    def test_walks_and_function_sorted_maps(self):
        d = FIX / "mini"
        model = check((d / "core.mcrl2").read_text(), (d / "codegen.toml").read_text(), _read(d / "lps.aterm"), [])
        self.assertEqual(sorted(model.walks), ["anyBusy", "bump"])
        bump, any_busy = model.walks["bump"], model.walks["anyBusy"]
        self.assertEqual((bump.pos, bump.op, bump.ret_pos, bump.overlays), (1, None, 0, []))
        self.assertEqual((any_busy.pos, any_busy.op, any_busy.ret_pos), (0, "||", None))
        self.assertEqual(model.fun_maps["tick"], ((0,), 0))
        self.assertEqual(model.in_place["c_submit"], [("uses", ["bump"])])
        self.assertNotIn("c_start", model.in_place)  # tick(tally, BUSY) is unfolded by the rewriter

    def test_constructor_fields_bind_sum_variables_and_unions_are_arguments(self):
        # c_start(job(x, uses(x))): x is bound by projecting job's first field (§3.5.5);
        # c_halt(y) carries the union Why (§3.5.7).
        d = FIX / "mini"
        model = check((d / "core.mcrl2").read_text(), (d / "codegen.toml").read_text(), _read(d / "lps.aterm"), [])
        self.assertEqual(model.summand_kind["c_start"], "output")
        self.assertEqual(model.summand_kind["c_halt"], "input")

    def test_init_is_computed_from_the_configuration(self):
        d = FIX / "mini"
        model = check((d / "core.mcrl2").read_text(), (d / "codegen.toml").read_text(), _read(d / "lps.aterm"), [])
        self.assertEqual(sorted(model.inits), ["slots"])
        self.assertEqual(model.inits["slots"].name, "gen_init_slots")

    def test_a_catalogue_entry_copied_verbatim_is_accepted(self):
        d = FIX / "cases" / "ok-catalogue"
        model = check((d / "core.mcrl2").read_text(), (d / "codegen.toml").read_text(), _read(d / "lps.aterm"),
                      read_catalogue(d / "catalogue"))
        self.assertEqual(model.catalogue_entries, ["capacity"])
        self.assertEqual([m.name for m in model.catalogue_maps], ["cap"])


class Example(unittest.TestCase):
    """The golden test: the example model's parsed form and its refusals (none)."""

    def _texts(self):
        return (EXAMPLE / "model" / "core.mcrl2").read_text(), (EXAMPLE / "codegen.toml").read_text()

    def test_example_matches_its_snapshot(self):
        a = _read(FIX / "jobqueue" / "lps.aterm")
        err = None
        try:
            check(*self._texts(), a, [])
        except GeneratorError as e:
            err = e
        self.assertEqual(render_lps(read_lps(a)) + render_refusals(err), (FIX / "jobqueue" / "expected.txt").read_text())

    def test_example_is_read_whole(self):
        lps = read_lps(_read(FIX / "jobqueue" / "lps.aterm"))
        self.assertEqual([n for n, _ in lps.params], ["w", "nj", "st", "tries", "Q", "slots", "entered", "byTries"])
        self.assertEqual([s.name for s in lps.summands],
                         ["c_submit", "c_finished", "c_crashed", "c_give_up", "c_retry", "c_start"])

    def test_example_is_accepted(self):
        model = check(*self._texts(), _read(FIX / "jobqueue" / "lps.aterm"), [])
        self.assertEqual([p.role for p in model.params],
                         ["config", "scalar", "column", "column", "frontier", "scalar", "table", "map"])
        self.assertEqual({k: v for k, v in model.summand_kind.items()},
                         {"c_submit": "input", "c_finished": "input", "c_crashed": "input",
                          "c_give_up": "output", "c_retry": "output", "c_start": "output"})
        self.assertEqual(sorted(m.name for m in model.user_maps), ["retries", "spent"])
        self.assertEqual(sorted(model.inits), ["slots"])
        self.assertEqual(model.walks, {})

    def test_the_maps_are_checked(self):
        lps = read_lps(_read(FIX / "jobqueue" / "lps.aterm"))
        from naima_codegen.config import read_config

        cfg, _ = read_config((EXAMPLE / "codegen.toml").read_text())
        rep = _Report()
        walks, fun_maps = _check_maps(lps, cfg, [m for m in lps.maps if not m.name.startswith("gen_init_")], rep)
        self.assertEqual(rep.items, [])
        self.assertEqual((walks, fun_maps), ({}, {}))


class Projects(unittest.TestCase):
    """A project is a directory; codegen.toml's [project] table places its files (§3.6.9)."""

    def _project(self, toml: str, where: str = "codegen.toml") -> Path:
        d = Path(tempfile.mkdtemp(prefix="naima-codegen-project-"))
        self.addCleanup(shutil.rmtree, d, True)
        (d / where).parent.mkdir(parents=True, exist_ok=True)
        (d / where).write_text('mcrl2_version = "202607.0"\n' + toml)
        return d

    def test_the_defaults(self):
        p = load(self._project("", "model/codegen.toml"))
        self.assertEqual((p.toml, p.model, p.generated), ("model/codegen.toml", "model", "generated"))
        self.assertEqual(p.cfg.project["rust_package"], None)
        self.assertEqual(p.problems, [])

    def test_a_table_at_the_root_places_the_model_and_the_outputs(self):
        p = load(self._project('[project]\nmodel = "spec"\ngenerated = "out/gen"\nrust_crate = "q"\npython_package = "q_py"\n'))
        self.assertEqual((p.toml, p.m("core.mcrl2"), p.gen_dir.name), ("codegen.toml", "spec/core.mcrl2", "gen"))
        self.assertEqual(p.cfg.project["rust_package"], "q")
        self.assertEqual(p.shown("model/core.mcrl2"), "spec/core.mcrl2")
        self.assertEqual(p.shown("model/codegen.toml"), "codegen.toml")

    def test_problems_of_the_table_are_g6(self):
        d = self._project('[project]\nmodel = "../x"\ncolour = "blue"\nrust_crate = "a b"\nrust_workspace = "yes"\n')
        problems = load(d).problems
        self.assertEqual(sorted(w for w, _, _ in problems),
                         ["project.colour", "project.model", "project.rust_crate", "project.rust_workspace"])
        d = self._project('[project]\nmodel = "spec"\n', "model/codegen.toml")
        self.assertEqual([w for w, _, _ in load(d).problems], ["project.model"])

    def test_one_declaration_only(self):
        d = self._project("")
        (d / "model").mkdir()
        (d / "model" / "codegen.toml").write_text("")
        with self.assertRaises(GeneratorError) as cm:
            load(d)
        self.assertEqual(cm.exception.codes, {"G6"})
        with self.assertRaises(GeneratorError):
            load(Path(tempfile.gettempdir()) / "naima-codegen-no-such-project")

    def test_the_names_are_required_to_build(self):
        from naima_codegen import outputs

        d = FIX / "mini"
        root = Path(_root_with((d / "core.mcrl2").read_text(), (d / "codegen-init.mcrl2").read_text(), (d / "codegen.toml").read_text()))
        self.addCleanup(shutil.rmtree, root, True)
        proj = load(root)
        model = run(proj, FakeTools(PIN, _read(d / "lps.aterm")))
        with self.assertRaises(GeneratorError) as cm:
            outputs.build(proj, model, PIN)
        self.assertEqual(sorted(x.where for x in cm.exception.diagnostics), ["project.python_package", "project.rust_crate"])

    def test_the_systems_table(self):
        from naima_codegen import systems

        p = load(self._project('[systems.groups]\nab = ["a", "b"]\n[systems.ids.Job]\nprefix = "J"\nbound = 3\n'))
        self.assertEqual(systems.groups({"a": "", "b": "", "c": ""}, p.cfg.systems["groups"]), {"ab": ["a", "b"], "c": ["c"]})
        with self.assertRaises(ValueError):
            systems.groups({"a": ""}, {"ab": ["a", "z"]})
        ids = systems.IdBounds(["Job"], p.cfg.systems["ids"])
        self.assertEqual(ids.bounds(["% @jobs 2\n", ""]), [("Job", "J", 3)])
        self.assertEqual(ids.bounds(["% @jobs 5\n"]), [("Job", "J", 5)])
        core, ctors = systems.finite_ids("sort Job = struct job(jx: Nat);\n", [("Job", "J", 2)])
        self.assertIn("sort Job = struct J0 | J1;", core)
        self.assertEqual(ctors, [("job", "J")])


class Refusals(unittest.TestCase):
    """One negative case per error code of the front end (§3.14.1)."""

    def test_every_case_gives_exactly_its_code(self):
        seen = set()
        for d in sorted((FIX / "cases").iterdir()):
            want = (d / "expect").read_text().strip()
            if want == "G2":
                continue
            # A case mcrl22lps refuses (§3.5.8) is judged from its source with mini's LPS.
            a = _read(d / "lps.aterm") if (d / "lps.aterm").exists() else _read(FIX / "mini" / "lps.aterm")
            with self.subTest(case=d.name):
                got = set()
                try:
                    check((d / "core.mcrl2").read_text(), (d / "codegen.toml").read_text(), a, read_catalogue(d / "catalogue"))
                except GeneratorError as e:
                    got = e.codes
                    for diag in e.diagnostics:
                        self.assertTrue(diag.file and diag.where and diag.section.startswith("§"), str(diag))
                self.assertEqual(got, {want} if want else set())
                seen |= got
        self.assertEqual(seen, {"G3", "G4", "G5", "G6", "G7", "G8", "G12"})

    def test_g1_toolset_version_differs_from_the_pin(self):
        d = FIX / "mini"
        root = _root_with((d / "core.mcrl2").read_text(), (d / "codegen-init.mcrl2").read_text(), (d / "codegen.toml").read_text())
        try:
            for version, words in (("201808.0", "201808.0"), (None, "not installed")):
                with self.assertRaises(GeneratorError) as cm:
                    run(root, FakeTools(version, _read(d / "lps.aterm")))
                self.assertEqual(cm.exception.codes, {"G1"})
                self.assertIn(words, str(cm.exception))
            model = run(root, FakeTools(PIN, _read(d / "lps.aterm")))
            self.assertEqual(len(model.lps.summands), 9)
        finally:
            shutil.rmtree(root)

    def test_g2_linearisation_fails(self):
        if not _pinned():
            self.skipTest("the pinned mCRL2 toolset %s is not installed" % PIN)
        d = FIX / "cases" / "g2-type-error"
        root = _root_with((d / "core.mcrl2").read_text(), (d / "codegen-init.mcrl2").read_text(), (d / "codegen.toml").read_text())
        try:
            with self.assertRaises(GeneratorError) as cm:
                run(root)
            self.assertEqual(cm.exception.codes, {"G2"})
            self.assertIn("mcrl22lps failed", str(cm.exception))
        finally:
            shutil.rmtree(root)

    def test_g4_sum_variable_named_like_a_parameter_before_linearising(self):
        # §3.5.8: mcrl22lps would fail (G2, "implicitly stated assignment"); the
        # generator refuses from the source first and never linearises.
        class NoLinearise(FakeTools):
            def linearise(self, text, name):
                raise AssertionError("linearised")

        d = FIX / "cases" / "g4-sum-variable-shadows-parameter"
        root = _root_with((d / "core.mcrl2").read_text(), (d / "codegen-init.mcrl2").read_text(), (d / "codegen.toml").read_text())
        try:
            with self.assertRaises(GeneratorError) as cm:
                run(root, NoLinearise(PIN))
            self.assertEqual(cm.exception.codes, {"G4"})
            self.assertIn("sum variable w", str(cm.exception))
        finally:
            shutil.rmtree(root)

    def test_every_front_end_code_has_a_case(self):
        cases = {(d / "expect").read_text().strip() for d in (FIX / "cases").iterdir()} - {""}
        # G1 has its own test above; G9 and G13, the policy's, are test_codegen_stepper.Policy's;
        # G10 is the construction's, G11 (the catalogue's layout limit) is not implemented yet
        # (G8's case is §3.4.8's in-place rule; its cost classes are not implemented yet).
        self.assertEqual(cases | {"G1"}, set(CODES) - {"G9", "G10", "G11", "G13"})


class WithTheToolset(unittest.TestCase):
    def setUp(self):
        if not _pinned():
            self.skipTest("the pinned mCRL2 toolset %s is not installed" % PIN)

    def test_fixtures_are_what_the_pinned_commands_make(self):
        make = _make_module()
        with tempfile.TemporaryDirectory() as t:
            make.build(Path(t))
            for p in sorted(Path(t).rglob("*")):
                if not p.is_file():
                    continue
                rel = p.relative_to(t)
                if rel.parts[0] == "cases" and rel.parts[1] in make.UNSTABLE and p.name == "lps.aterm":
                    d = FIX / rel.parent
                    self.assertEqual(_codes(d, p.read_text()), _codes(d, (FIX / rel).read_text()), str(rel))
                else:
                    self.assertEqual(p.read_text(), (FIX / rel).read_text(), str(rel))
        self.assertEqual(make.example_lps(), _read(FIX / "jobqueue" / "lps.aterm"))

    def test_mini_through_the_toolset_is_accepted(self):
        d = FIX / "mini"
        root = _root_with((d / "core.mcrl2").read_text(), (d / "codegen-init.mcrl2").read_text(), (d / "codegen.toml").read_text())
        try:
            self.assertEqual([s.name for s in run(root).lps.summands], [s.name for s in read_lps(_read(d / "lps.aterm")).summands])
        finally:
            shutil.rmtree(root)

    def test_the_example_is_accepted(self):
        model = run(EXAMPLE)
        self.assertEqual(len(model.lps.summands), 6)

    def test_the_command(self):
        cmd = [sys.executable, str(ROOT / "naima-codegen")]
        self.assertEqual(subprocess.run(cmd + ["generate", "--nope"], capture_output=True).returncode, 2)
        self.assertEqual(subprocess.run(cmd + ["nope"], capture_output=True).returncode, 2)
        r = subprocess.run(cmd + ["generate", "--front-end", "--project", str(EXAMPLE)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("6 summands (3 input, 3 output)", r.stdout)
        r = subprocess.run([sys.executable, "-m", "naima_codegen", "generate", "--front-end"], cwd=EXAMPLE,
                           env=dict(os.environ, PYTHONPATH=str(ROOT)), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_generated_is_what_the_generator_writes(self):
        # §3.15.3: each example's generated/ is regenerated byte for byte from its committed inputs.
        for ex in (EXAMPLE, ROOT / "examples" / "form"):
            cmd = [sys.executable, str(ROOT / "naima-codegen"), "generate", "--check", "--project", str(ex)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


class Headers(unittest.TestCase):
    """§3.1.3: every generated file carries the header block in its comment syntax."""

    def test_every_generated_file_of_the_examples_carries_the_header(self):
        from naima_codegen.header import block
        prefix = {".rs": "//", ".py": "#", ".toml": "#", ".mcrl2": "%", ".mcf": "%", ".ts": "//"}
        files = [f for ex in (EXAMPLE, ROOT / "examples" / "form") for f in sorted((ex / "generated").rglob("*"))
                 if f.is_file() and f.name != "MANIFEST.json"]
        self.assertTrue(files)
        for f in files:
            text, head = f.read_text(), block(prefix[f.suffix])
            with self.subTest(f=str(f.relative_to(ROOT))):
                if f.name.endswith("-model.mcrl2"):  # the plain Core in a system: the system's own header only
                    self.assertTrue(text.startswith("% GENERATED by naima-codegen"))
                elif f.parent.name == "systems":  # the system's header, then the refined or probe Core's
                    self.assertIn(head, text)
                else:
                    self.assertTrue(text.startswith(head))

    def test_the_block_is_one_comment_line_each(self):
        from naima_codegen.header import LINES, block
        for p in ("//", "#", "%"):
            lines = block(p).splitlines()
            self.assertEqual(len(lines), len(LINES))
            self.assertTrue(all(ln.startswith(p + " ") for ln in lines))


if __name__ == "__main__":
    unittest.main()
