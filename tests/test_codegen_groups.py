"""Indexes that admit by a per-group count (docs/spec.md §3.7.6), on the fixture model
tests/fixtures/grouppool: KONE items hold one slot per group (occg at the item's group),
KANY items run at once; `ready` (newest first) and `readyHi` (within it) admit a KONE
item by its group's slot.

- the policy's checks: a group that is not a Nat column read at the member, an
  admits_group that reads a column, a scalar or a table at another argument, a group
  without its admission or without a class: G9;
- the reference definitions of the refined Core read the group as the code does;
- the refined Core serves a ready item of a free group while another group is busy
  (the non-blocking probe of §3.9.3 finds no state where the model runs an item and
  the refined Core waits), where the same policy admitting KONE by its global count
  blocks;
- a scripted sequence (a group freed, a high-priority item of a busy group) gives the
  expected actions in the Python backend in audit mode, and Rust, Python and
  TypeScript take the same actions and hold states with the same encoding on it and
  on seeded random sequences.

The end-to-end tests need the pinned mCRL2 toolset (and cargo, deno), and skip,
saying so, without them.
"""

import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "grouppool"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from naima_codegen.policy import check_policy, read_policy  # noqa: E402
from naima_codegen.toolchain import Toolchain  # noqa: E402

PIN = "202607.0"
POLICY = (FIXTURE / "model" / "policy.toml").read_text()
GROUPED = ('admits = { var = "k", when = "k == KONE || room(k, occ(k))" }\ngroup = "grp(n)"\n'
           'admits_group = { var = "g", when = "k != KONE || room(k, occg(g))" }')
# The same policy admitting KONE by its global count: what the generator could say before groups.
CLASS_ONLY = POLICY.replace(GROUPED, 'admits = { var = "k", when = "room(k, occ(k))" }')
PARAMS = {"ni": "Nat", "st": "Item -> Nat", "kd": "Item -> Kind", "grp": "Item -> Nat", "hi": "Item -> Bool",
          "occ": "Kind -> Nat", "occg": "Nat -> Nat"}
COLUMNS = {"st", "kd", "grp", "hi"}
KANY, KONE, G0, G1 = 0, 1, 0, 1


def _g9(text):
    p, probs = read_policy(text)
    if probs:
        return [m for _, _, m in probs]
    return [d.message for d in check_policy(p, {"c_run": (["i"], [])}, PARAMS, COLUMNS, set(), {"Item": "item"}, set())]


class Checks(unittest.TestCase):
    def test_the_fixture_policy_is_accepted(self):
        self.assertEqual(POLICY.count(GROUPED), 2)
        self.assertEqual(_g9(POLICY), [])
        self.assertEqual(_g9(CLASS_ONLY), [])

    def test_what_is_not_local_is_g9(self):
        for old, new, why in (
                ('group = "grp(n)"', 'group = "grp(item(ix(n) + 1))"', "not local"),
                ('group = "grp(n)"', 'group = "occ(kd(n))"', "column of sort Nat"),
                ("room(k, occg(g))", "room(k, grp(item(0)))", "column grp"),
                ("room(k, occg(g))", "room(k, occg(g + 1))", "another argument"),
                ("room(k, occg(g))", "room(k, ni)", "not a table"),
                ("room(k, occg(g))", "room(k, occ(k))", "not over Nat")):
            with self.subTest(new=new):
                msgs = _g9(POLICY.replace(old, new, 1))
                self.assertTrue(any(why in m for m in msgs), msgs)

    def test_a_group_comes_with_its_admission_and_a_class(self):
        msgs = _g9(POLICY.replace('\nadmits_group = { var = "g", when = "k != KONE || room(k, occg(g))" }', "", 1))
        self.assertTrue(any("group and admits_group come together" in m for m in msgs), msgs)
        msgs = _g9(POLICY.replace('class = "kd(n)"\nadmits = { var = "k", when = "k == KONE || room(k, occ(k))" }\n', "", 1))
        self.assertTrue(any("only with a class" in m for m in msgs), msgs)


def _pinned() -> bool:
    return Toolchain().version() == PIN


@unittest.skipUnless(_pinned(), "the pinned mCRL2 toolset %s is not installed" % PIN)
class Generated(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from naima_codegen.frontend import run
        from naima_codegen.lower import Types
        from naima_codegen.project import load
        from test_codegen_stepper import _python_backend

        cls.dir = Path(tempfile.mkdtemp(prefix="naima-codegen-grouppool-"))
        cls.proj_dir = cls.dir / "proj"
        shutil.copytree(FIXTURE, cls.proj_dir)
        r = subprocess.run([sys.executable, "-m", "naima_codegen", "generate", "--project", str(cls.proj_dir)],
                           capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(ROOT)))
        if r.returncode:
            raise AssertionError(r.stdout[-2000:] + r.stderr[-2000:])
        cls.proj = load(cls.proj_dir)
        cls.model = run(cls.proj_dir)
        cls.types = Types(cls.model.lps, cls.model.config.id_sorts)
        pj = cls.proj.cfg.project
        cls.gen = cls.proj.gen_dir
        cls.py = _python_backend(cls.gen, pj["python_package"])
        cls.rust_package, cls.rust_crate = pj["rust_package"], cls.gen / "rust" / pj["rust_crate"]
        cls.plain = cls.gen / "typescript" / pj["typescript_module"] / ("%s.ts" % pj["typescript_module"])
        cls.inputs = sorted((a for a in cls.model.actions if a.kind == "input"), key=lambda a: a.label)
        cls.outputs = sorted((a for a in cls.model.actions if a.kind == "output"), key=lambda a: a.label)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_the_reference_definitions_read_the_group(self):
        text = (self.gen / "model" / "core-refined.mcrl2").read_text()
        self.assertIn("eqn ix_admg_ready(ix_p_occg, ix_k, ix_g) = ix_k != KONE || room(ix_k, ix_p_occg(ix_g));", text)
        walk = next(x for x in text.splitlines() if x.strip().startswith("ix_first_ready(ix_y |> ix_l"))
        self.assertIn("ix_admg_ready(ix_p_occg, ix_cls_ready(ix_p_kd, ix_y), ix_grp_ready(ix_p_grp, ix_y))", walk)

    def _blocked(self, policy_text) -> int:
        from naima_codegen import systems
        from naima_codegen.checks import blocked_states, explore, read_aut
        from naima_codegen.refine import refine

        m = FIXTURE / "model"
        ids = systems.IdBounds(self.model.config.id_sorts, self.proj.cfg.systems.get("ids"))
        r = refine((m / "core.mcrl2").read_text(), read_policy(policy_text)[0])
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "p.mcrl2"
            src.write_text(systems.assemble(r.probe, (m / "env.mcrl2").read_text(), [("two", (m / "instances" / "two.mcrl2").read_text())],
                                            ids, init_suffix=r.init_suffix,
                                            allow=["probe_enabled", "probe_quiet", "probe_ambiguous"]
                                            + ["probe_deferred_%d" % j for j in range(len(r.deferred))]
                                            + sorted({"%s|%s" % (v, mm) for v, mm, _ in r.synced})))
            n, edges = read_aut(explore(src, Path(d)))
        return len(blocked_states(n, edges))

    def test_a_free_group_is_served_while_another_is_busy(self):
        """§3.9.3 on the instance two: no blocked state with the group admission; the
        class-only admission blocks where group 1 is free and group 0 busy."""
        self.assertEqual(self._blocked(POLICY), 0)
        self.assertGreater(self._blocked(CLASS_ONLY), 0)

    SCRIPT = [((0, 0, KONE, G0, False), [(0, 0)]),
              ((0, 1, KONE, G0, True), []),          # group 0 busy
              ((0, 2, KONE, G1, False), [(0, 2)]),   # group 1 free: served while group 0 is busy
              ((0, 3, KONE, G1, False), []),
              ((0, 4, KANY, G0, False), [(0, 4)]),   # another class: its own count
              ((0, 5, KONE, G0, False), []),
              ((1, 2), [(0, 3)]),                    # group 1 freed: its waiting item
              ((1, 0), [(0, 1)]),                    # group 0 freed: the high-priority item before the newer 5
              ((1, 1), [(0, 5)])]

    def test_the_script_in_python_with_the_audit(self):
        m = self.py
        out = []
        st = m.State(m.Config(), out, True)
        self.assertEqual(out, [])
        for ev, want in self.SCRIPT:
            out = []
            st.step(ev, out)
            self.assertEqual(out, want, ev)

    def _sequences(self, n, seed):
        rnd = random.Random(seed)
        seqs = [({}, [ev for ev, _ in self.SCRIPT])]
        for _ in range(n):
            evs, k = [], 0
            for _ in range(rnd.randrange(10, 40)):
                if k < 12 and rnd.random() < 0.55:
                    evs.append((0, k, rnd.choice((KANY, KONE, KONE)), rnd.choice((G0, G1)), rnd.random() < 0.3))
                    k += 1
                else:
                    evs.append((1, rnd.randrange(max(1, k))))
            seqs.append(({}, evs))
        return seqs

    def _python(self, seqs):
        m = self.py
        rows_all = []
        for config, events in seqs:
            acts = []
            st = m.State(m.Config(**config), acts, True)
            rows = ["init %s %s" % (",".join(m.encode((1, a)).hex() for a in acts), st.state_bytes().hex())]
            for ev in events:
                acts = []
                try:
                    st.step(ev, acts)
                except m.AuditFailed:
                    raise
                except m.StepError as x:
                    rows.append("err %s" % type(x).__name__)
                    if not isinstance(x, m.Refused):
                        break
                    continue
                rows.append("ok %s %s" % (",".join(m.encode((1, a)).hex() for a in acts), st.state_bytes().hex()))
            rows_all.append(rows)
        return rows_all

    @unittest.skipIf(shutil.which("cargo") is None, "needs cargo")
    def test_the_backends_agree(self):
        import codegen_ts as ts
        from test_codegen_stepper import _run_rust_sequences

        if ts.deno() is None:
            self.skipTest("deno is not installed")
        seqs = self._sequences(60, 20261031)
        py = self._python(seqs)
        rs = _run_rust_sequences(self.model, self.types, self.py, seqs, self.rust_package, self.rust_crate)
        data = [{"config": c, "events": [ts.ts_entry(0, ev, self.inputs, self.outputs, self.types)["event"] for ev in evs]}
                for c, evs in seqs]
        tsr, cur = [], []
        for line in ts.run_json("replay.ts", data, self.plain).splitlines():
            if line == "-":
                tsr.append(cur)
                cur = []
            else:
                cur.append(line)
        self.assertEqual((len(rs), len(tsr)), (len(py), len(py)))
        n = 0
        for k, (a, b, c) in enumerate(zip(py, rs, tsr)):
            self.assertEqual((b, c), (a, a), "sequence %d: Python, Rust, TypeScript" % k)
            n += len(a)
        self.assertGreater(n, 600)


if __name__ == "__main__":
    unittest.main()
