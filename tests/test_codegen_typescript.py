"""The TypeScript backend (docs/spec.md §3.18) against mCRL2, and the three
backends against each other (§3.16), on examples/jobqueue and examples/form.

- the generated modules type-check under Deno with Svelte's rune declarations;
  the plain module imports nothing and the Svelte module only the plain one;
  the Svelte module compiles with Svelte's compiler without a warning and, run
  headless, does on every event what the plain module's step does: the method
  says whether the event was accepted, the port gets the actions in order, the
  view holds the new state and an effect reading it runs again (§3.18.4);
- every user map of the mini fixture and of the job queue gives the value
  mCRL2's rewriter gives, on seeded random arguments (§3.4.8);
- the state space explored from each example's transition table, on bounded
  ids, is strongly bisimilar to the refined Core's (mcrl22lps, lps2lts,
  ltscompare -ebisim), and so is the Python backend's stepper's (§3.18.5);
- the acceptor accepts every entry of seeded random paths of the examples'
  model systems, with the bytes the Python backend writes (§3.8.9, §3.8.10);
- Rust, Python and TypeScript take the same actions, raise the same error at
  the same event and hold states with the same encoding, after every event of
  seeded random sequences: walks of the refined systems with, at random, an
  event replaced by a random one of a random label, legal or not (§3.16.1).

Needs the pinned mCRL2 toolset and Deno 2 (the Svelte and TypeScript packages
the drivers import are fetched by Deno once and cached); the cross-check also
needs cargo. Each test says what it skips for.
"""

import copy
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import codegen_ts as ts  # noqa: E402
from codegen_values import Gen, parse_printed, to_python, unbind_ids  # noqa: E402
from naima_codegen.frontend import run  # noqa: E402
from naima_codegen.lower import Types  # noqa: E402
from naima_codegen.toolchain import Toolchain  # noqa: E402

PIN = "202607.0"
OBSERVERS = ("o_",)


def _int_config(name, mcrl2_map):
    return lambda text: {name: int(re.search(r"%s\(@I\) = (\d+);" % mcrl2_map, text).group(1))}


# Per example: the configuration of an instance, the state spaces compared (configuration, id bounds),
# and the groups whose systems the walks come from.
EXAMPLES = {
    "jobqueue": (_int_config("w", "I_w"), [({"w": 1}, {"Job": 2}), ({"w": 2}, {"Job": 3})], ("small", "flaky")),
    "form": (_int_config("limit", "I_limit"), [({"limit": 1}, {}), ({"limit": 3}, {})], ("small",)),
}


def _skip():
    if Toolchain().version() != PIN:
        raise unittest.SkipTest("the pinned mCRL2 toolset %s is not installed" % PIN)
    if ts.deno() is None:
        raise unittest.SkipTest("deno is not installed")


class _Example:
    """An example's model, its generated files and the Python backend loaded."""

    def __init__(self, name):
        from naima_codegen.project import load
        from test_codegen_stepper import _python_backend

        self.name = name
        self.root = ROOT / "examples" / name
        self.proj = load(self.root)
        self.model = run(self.root)
        self.types = Types(self.model.lps, self.model.config.id_sorts)
        pj = self.proj.cfg.project
        self.gen = self.proj.gen_dir
        self.module = pj["typescript_module"]
        self.plain = self.gen / "typescript" / self.module / ("%s.ts" % self.module)
        self.svelte = self.gen / "typescript" / self.module / ("%s.svelte.ts" % self.module)
        self.py = _python_backend(self.gen, pj["python_package"])
        self.rust_package, self.rust_crate = pj["rust_package"], self.gen / "rust" / pj["rust_crate"]
        self.inputs = sorted((a for a in self.model.actions if a.kind == "input"), key=lambda a: a.label)
        self.outputs = sorted((a for a in self.model.actions if a.kind == "output"), key=lambda a: a.label)
        self.config, self.spaces, self.groups = EXAMPLES[name]

    def sequences(self, n, seed):
        """Seeded random event sequences: walks of the refined systems projected to events, an event
        replaced at random by a random one of a random label (legal or not). [(configuration, [event])]."""
        from test_codegen_stepper import _entry, _walks

        rnd = random.Random(seed)
        gen = Gen(self.types, self.py, seed + 1)
        insts = {p.stem: p.read_text() for p in (self.root / "model" / "instances").glob("*.mcrl2")}
        ids = self.proj.cfg.systems["ids"]
        out = []
        for group in self.groups:
            for labels in _walks(self.gen / "model" / "systems" / ("%s-refined.mcrl2" % group), seed + 2)[:n // len(self.groups)]:
                pick = re.fullmatch(r"pick\(i_(\w+)\)", labels[0])
                events = []
                for lbl in labels[1:]:
                    e = (_entry(unbind_ids(lbl, self.model, ids), self.inputs, self.outputs, self.types)
                         if not lbl.startswith(OBSERVERS) else None)
                    if e is None or e[0] != 0:
                        continue
                    if rnd.random() < 0.15:
                        k = rnd.randrange(len(self.inputs))
                        e = (0, (k,) + tuple(gen.value(srt)[1] for srt in self.inputs[k].sorts))
                    events.append(e[1])
                out.append((self.config(insts[pick.group(1)]), events))
        return out

    def ts_event(self, ev):
        return ts.ts_entry(0, ev, self.inputs, self.outputs, self.types)["event"]


_CACHE: dict = {}


def _example(name) -> _Example:
    if name not in _CACHE:
        _CACHE[name] = _Example(name)
    return _CACHE[name]


class Modules(unittest.TestCase):
    """§3.18.1, §3.18.4: what the modules import, that they type-check, and the Svelte module run headless."""

    @classmethod
    def setUpClass(cls):
        _skip()

    def test_the_modules_type_check(self):
        from naima_codegen import tsback
        from test_codegen_python import _mini

        with tempfile.TemporaryDirectory(prefix="naima-ts-") as d:
            mini = Path(d) / "mini.ts"
            mini.write_text(tsback.emit(_mini(), None, "mini")["mini.ts"])
            mods = [mini] + [p for n in EXAMPLES for p in (_example(n).plain, _example(n).svelte)]
            r = ts.check(*mods)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])

    def test_the_plain_module_imports_nothing_and_the_svelte_module_only_it(self):
        for n in EXAMPLES:
            e = _example(n)
            plain, svelte = e.plain.read_text(), e.svelte.read_text()
            self.assertEqual(re.findall(r"(?m)^\s*import\b.*", plain), [])
            self.assertEqual(set(re.findall(r'(?m)^import .* from "([^"]+)";', svelte)), {"./%s.ts" % e.module})
            self.assertNotIn("$state", plain)
            for bad in ("Deno.", "fetch(", "window", "document", "setTimeout", "Date", "Math.random", "console", "await "):
                self.assertNotIn(bad, plain + svelte, "%s: %s" % (n, bad))
            for fn in re.findall(r"(?m)^function (_m_\w+)\(", plain):
                body = plain.split("function %s(" % fn, 1)[1].split("\nfunction ", 1)[0]
                self.assertNotIn(fn + "(", body, fn)

    def test_the_svelte_module_does_what_the_step_does(self):
        for n in EXAMPLES:
            e = _example(n)
            seqs = [{"config": c, "events": [e.ts_event(ev) for ev in evs]} for c, evs in e.sequences(40, 20261023)]
            out = ts.run_json("svelte.ts", seqs, e.svelte).strip()
            self.assertTrue(out.startswith("ok "), "%s: %s" % (n, out))
            self.assertGreater(int(out.split()[1]), 100, n)


class _Maps:
    """Every user map against mCRL2's rewriter, in the TypeScript backend."""

    @classmethod
    def setUpClass(cls):
        from naima_codegen import pyback
        from test_codegen_python import _cases_and_mcrl2, _load

        _skip()
        cls.root, cls.tmp = cls.project()
        cls.model = run(cls.root)
        cls.types = Types(cls.model.lps, cls.model.config.id_sorts)
        cls.cases, cls.printed = _cases_and_mcrl2(cls.model, _load(pyback.emit(cls.model)), cls.types, cls.root, 12)

    @classmethod
    def tearDownClass(cls):
        if cls.tmp:
            shutil.rmtree(cls.root, ignore_errors=True)

    def test_typescript_backend_agrees_with_the_mcrl2_rewriter(self):
        from naima_codegen import tsback

        t = self.types
        data = []
        for m, pyargs, pts, rsort, n, first in self.cases:
            data.append({"map": m.name, "args": [ts.py2ts(v, s, t) for v, s in zip(pyargs, m.sort[1])],
                         "points": None if pts is None else [ts.py2ts(p, t.resolve(m.sort[2])[1][0], t) for _, p in pts]})
        with tempfile.TemporaryDirectory(prefix="naima-ts-") as d:
            mod = Path(d) / "m.ts"
            mod.write_text(tsback.emit(self.model, None, "m")["m.ts"])
            lines = ts.run_json("maps.ts", data, mod).splitlines()
        self.assertEqual(len(lines), len(self.cases))
        for (m, pyargs, pts, rsort, n, first), line in zip(self.cases, lines):
            got = json.loads(line)
            self.assertIsInstance(got, list, "%s on %r: %r" % (m.name, pyargs, got))
            want = [to_python(parse_printed(lbl), rsort, t) for lbl in self.printed[first:first + n]]
            self.assertEqual([ts.ts2py(v, rsort, t) for v in got], want, "%s on %r" % (m.name, pyargs))


class MiniMaps(_Maps, unittest.TestCase):
    @staticmethod
    def project():
        from test_codegen_python import _mini_root

        return _mini_root(), True


class ExampleMaps(_Maps, unittest.TestCase):
    @staticmethod
    def project():
        return str(ROOT / "examples" / "jobqueue"), False


def _mcrl2_value(v) -> str:
    return ("true" if v else "false") if isinstance(v, bool) else str(v)


def _refined_lts(e: _Example, config: dict, ids: dict, d: Path) -> Path:
    """The refined Core on its own, ids bounded, started by the environment's CoreStart with the
    configuration: every event at every quiescent state, as the transition table is explored. Its
    .aut with the ids written back as the model writes them."""
    from naima_codegen.policy import read_policy
    from naima_codegen.refine import matching, refine
    from naima_codegen.systems import IdBounds, finite_ids

    core = (e.root / "model" / "core.mcrl2").read_text()
    suffix = refine(core, read_policy((e.root / "model" / "policy.toml").read_text())[0]).init_suffix
    text, _ = finite_ids((e.gen / "model" / "core-refined.mcrl2").read_text(),
                         [(s, p, ids[s]) for s, p, _, _ in IdBounds(e.model.config.id_sorts, e.proj.cfg.systems["ids"]).sorts])
    env = (e.root / "model" / "env.mcrl2").read_text()
    k = env.index("proc CoreStart")
    start = env[k:env.index(";", k) + 1]
    c = start.index("Core(")
    end = matching(start, c + 4)
    start = start[:end - 1] + suffix + start[end - 1:]
    cfg = [p.name for p in e.model.params if p.role == "config"]
    (d / "m.mcrl2").write_text(text + "\n" + start + "\n\ninit CoreStart(%s);\n" % ", ".join(_mcrl2_value(config[n]) for n in cfg))
    for cmd in (["mcrl22lps", "-q", "m.mcrl2", "m.lps"], ["lps2lts", "-q", "m.lps", "raw.aut"]):
        r = subprocess.run(cmd, cwd=d, capture_output=True, text=True)
        if r.returncode:
            raise AssertionError(r.stderr[-2000:])
    lines = (d / "raw.aut").read_text().splitlines()
    (d / "model.aut").write_text("\n".join(lines[:1] + [unbind_ids(x, e.model, e.proj.cfg.systems["ids"]) for x in lines[1:]]) + "\n")
    return d / "model.aut"


def _py_label(a, args, e: _Example) -> str:
    """A Python entry's label as mCRL2 prints it, ids as the model writes them."""
    t = e.types

    def text(v, s):
        s = t.resolve(s)
        k = t.kind(s)
        if k == "bool":
            return "true" if v else "false"
        if k == "int":
            return str(v)
        if k in ("List", "FSet"):
            items = list(v) if k == "List" else sorted(v)
            return ("[%s]" if k == "List" else "{%s}") % ", ".join(text(x, s[1]) for x in items)
        cons = t.lps.structs[s[1]].constructors
        if k == "id":
            return "%s(%d)" % (cons[0][0], v)
        if k == "enumeration":
            return cons[v][0]
        c, vals = (cons[0], v) if k == "record" else (cons[v[0]], v[1:])
        return c[0] + ("(%s)" % ", ".join(text(x, fs) for x, (_, fs) in zip(vals, c[1])) if c[1] else "")

    return a.label + ("(%s)" % ", ".join(text(v, s) for v, s in zip(args, a.sorts)) if a.sorts else "")


def _py_lts(e: _Example, config: dict, ids: dict, out: Path):
    """The Python stepper's state space, explored as explore.ts explores the TypeScript one: from the
    state before the constructor's output loop, the first enabled output in policy order, else every
    event of the finite domains on every input summand."""
    m = e.py
    t = e.types

    def values(s):
        s = t.resolve(s)
        k = t.kind(s)
        if k == "bool":
            return [False, True]
        if k == "id":
            return list(range(ids[s[1]]))
        if k == "enumeration":
            return list(range(len(t.lps.structs[s[1]].constructors)))
        if k == "int":
            return list(range(1 if s[1] == "Pos" else 0, 4))
        raise AssertionError("no finite domain for %r" % (s,))

    events = []
    for code, a in enumerate(e.inputs):
        vs = [()]
        for s in a.sorts:
            vs = [x + (y,) for x in vs for y in values(s)]
        events += [(code, a, x) for x in vs]

    def key(s):
        return m._state_bytes(s).hex() + "|" + "|".join(
            ",".join(map(str, getattr(s, n).ordered())) for n in type(s).__slots__ if n.startswith("_ix_"))

    s0 = m._initial(m.Config(**config))
    m._stepper_init(s0, False)
    index, queue, edges = {key(s0): 0}, [s0], []

    def ident(s):
        k = key(s)
        if k not in index:
            index[k] = len(index)
            queue.append(s)
        return index[k]

    i = 0
    while i < len(queue):
        s = queue[i]
        fired = False
        for f in m._OUTS:
            c, acts = copy.deepcopy(s), []
            if f(c, acts):
                m._refresh(c)
                edges.append('(%d,"%s",%d)' % (i, _py_label(e.outputs[acts[0][0]], acts[0][1:], e), ident(c)))
                fired = True
                break
        if not fired:
            for code, a, args in events:
                for match, fire in m._STEP.get(code, ()):
                    c = copy.deepcopy(s)
                    env = match(c, args)
                    if env is not None:
                        fire(c, env)
                        m._refresh(c)
                        edges.append('(%d,"%s",%d)' % (i, _py_label(a, args, e), ident(c)))
        i += 1
    out.write_text("\n".join(["des (0,%d,%d)" % (len(edges), len(index))] + edges) + "\n")
    return len(index)


class StateSpace(unittest.TestCase):
    """§3.18.5: the state space of the generated stepper is strongly bisimilar to the refined Core's.
    Strong, not branching: neither side has an internal step to abstract from; every summand fired is a
    labelled transition on both, inputs in quiescent states and outputs in policy order."""

    @classmethod
    def setUpClass(cls):
        _skip()

    def _compare(self, name):
        e = _example(name)
        sizes = []
        for config, ids in e.spaces:
            with tempfile.TemporaryDirectory(prefix="naima-lts-") as d:
                d = Path(d)
                model = _refined_lts(e, config, ids, d)
                (d / "config.json").write_text(json.dumps(config))
                (d / "domains.json").write_text(json.dumps({"ids": ids, "ints": [0, 3]}))
                got = json.loads(ts.run("explore.ts", e.plain, d / "config.json", d / "domains.json", d / "ts.aut"))
                n_py = _py_lts(e, config, ids, d / "py.aut")
                for aut in ("ts.aut", "py.aut"):
                    r = subprocess.run(["ltscompare", "-q", "-ebisim", aut, "model.aut"], cwd=d, capture_output=True, text=True)
                    self.assertEqual(r.stdout.strip(), "true", "%s %r %r: %s against the refined Core: %s"
                                     % (name, config, ids, aut, r.stdout + r.stderr[-2000:]))
                self.assertEqual(n_py, got["states"])
                sizes.append(got["states"])
        return sizes

    def test_jobqueue(self):
        self.assertGreater(max(self._compare("jobqueue")), 200)

    def test_form(self):
        self.assertGreater(max(self._compare("form")), 15)


class Traces(unittest.TestCase):
    """§3.8.9, §3.8.10: the TypeScript acceptor accepts every entry of random paths of the model
    systems, and the entries' bytes are the Python backend's and decode back."""

    @classmethod
    def setUpClass(cls):
        _skip()

    def test_entries_are_accepted_and_encode_as_in_python(self):
        from test_codegen_python import _group_walks

        for name in EXAMPLES:
            e = _example(name)
            runs, want = [], []
            for g in e.groups:
                for inst, config, entries in _group_walks(e.model, e.types, g, e.root, e.config):
                    runs.append({"config": config, "entries": [
                        ts.ts_entry(side, (code,) + tuple(args), e.inputs, e.outputs, e.types)
                        for side, _, code, args, _ in entries]})
                    want += ["%s true ok" % e.py.encode((side, (code,) + tuple(args))).hex() for side, _, code, args, _ in entries]
            got = ts.run_json("traces.ts", runs, e.plain).splitlines()
            self.assertEqual(got, want, name)
            self.assertGreater(len(got), 100, name)


class CrossCheck(unittest.TestCase):
    """§3.16.1, §3.16.5: Rust (built natively, events decoded from the Python backend's bytes), Python
    and TypeScript take the same actions, raise the same error at the same event and hold states with
    the same encoding after every event."""

    @classmethod
    def setUpClass(cls):
        _skip()
        if shutil.which("cargo") is None:
            raise unittest.SkipTest("cargo is not installed")

    def _python(self, e, seqs):
        m = e.py
        out = []
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
            out.append(rows)
        return out

    def _typescript(self, e, seqs):
        data = [{"config": c, "events": [e.ts_event(ev) for ev in evs]} for c, evs in seqs]
        out, cur = [], []
        for line in ts.run_json("replay.ts", data, e.plain).splitlines():
            if line == "-":
                out.append(cur)
                cur = []
            else:
                cur.append(line)
        return out

    def _check(self, name):
        from test_codegen_stepper import _run_rust_sequences

        e = _example(name)
        seqs = e.sequences(120, 20261009)
        py = self._python(e, seqs)
        rs = _run_rust_sequences(e.model, e.types, e.py, seqs, e.rust_package, e.rust_crate)
        tsr = self._typescript(e, seqs)
        self.assertEqual((len(rs), len(tsr)), (len(py), len(py)))
        n = 0
        for k, (a, b, c) in enumerate(zip(py, rs, tsr)):
            for i, (x, y, z) in enumerate(zip(a, b, c)):
                self.assertEqual((y, z), (x, x), "%s sequence %d, event %d: Python, Rust, TypeScript" % (name, k, i))
            self.assertEqual((len(b), len(c)), (len(a), len(a)), "%s sequence %d" % (name, k))
            n += len(a)
        return n

    def test_jobqueue(self):
        self.assertGreater(self._check("jobqueue"), 400)

    def test_form(self):
        self.assertGreater(self._check("form"), 400)


if __name__ == "__main__":
    unittest.main()
