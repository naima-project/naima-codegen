"""The generator's Python and Rust backends against mCRL2 and against each other
(docs/spec.md §3.4.8, §3.8.1, §3.8.9, §3.12, §3.11).

- the mini Core's acceptor accepts the entries the model allows, rejects the
  others and fires the summand's next state (in-place walks, overlays,
  constructor-bound variables, `init`);
- every user map of the mini fixture and of the example model gives, on seeded
  random arguments, the value mCRL2's own rewriter gives (`lps2lts` on a
  process that prints them), in both backends: the walks compiled into loops
  are the model's equations;
- every entry of seeded random paths of the example's model systems is
  accepted by the acceptor of both backends.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import types as pytypes
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "codegen"
EXAMPLE = ROOT / "examples" / "jobqueue"
CRATE = "generated_under_test"
TARGET = Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "target" / "codegen-tests"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codegen_values import Gen, parse_printed, to_python, unbind_ids  # noqa: E402
from naima_codegen import pyback  # noqa: E402
from naima_codegen.frontend import check, run  # noqa: E402
from naima_codegen.lower import Types  # noqa: E402
from naima_codegen.toolchain import Toolchain  # noqa: E402

PIN = "202607.0"


def _load(src: str):
    mod = pytypes.ModuleType("generated_under_test")
    exec(compile(src, "generated_under_test", "exec"), mod.__dict__)
    return mod


def _mini():
    d = FIX / "mini"
    return check((d / "core.mcrl2").read_text(), (d / "codegen.toml").read_text(), (d / "lps.aterm").read_text(), [])


class MiniAcceptor(unittest.TestCase):
    def setUp(self):
        self.mod = _load(pyback.emit(_mini()))
        self.acc = self.mod.Acceptor(self.mod.Config(w=2))
        m = self.mod
        self.ev = lambda code, *a: (0, (code,) + a)
        self.act = lambda code, *a: (1, (code,) + a)
        self.m = m

    def test_generation_is_deterministic(self):
        self.assertEqual(pyback.emit(_mini()), pyback.emit(_mini()))

    def test_initial_state_and_init(self):
        self.assertEqual(self.acc.read("slots"), 4)  # init = "2 * w" (§3.6.2)
        self.assertEqual(self.acc.read("n"), 0)

    def test_a_run_is_accepted_and_fires_next_states(self):
        m, a = self.m, self.acc
        a.accept(self.ev(m.EV_SUBMIT, 0, (1, 2)))  # submit(node(0), [node(1), node(2)])
        self.assertEqual(a.read("n"), 1)
        self.assertEqual(a.read("RQ"), frozenset({0}))
        uses = a.read("uses")  # bump(uses, l): a walk updating the column in place
        self.assertEqual([uses.get(k) for k in range(3)], [0, 1, 1])
        a.accept(self.ev(m.EV_SUBMIT, 1, (2, 2)))
        self.assertEqual([uses.get(k) for k in range(3)], [0, 1, 3])
        # start(job(node(1), uses(node(1)))): node(1) bound through the record job (§3.5.5)
        a.accept(self.act(m.ACT_START, (1, 1)))
        self.assertEqual(a.read("slots"), 3)
        self.assertEqual(a.read("tally").get(1), 1)

    def test_entries_the_model_does_not_allow_are_rejected(self):
        m, a = self.m, self.acc
        with self.assertRaises(m.Rejected):
            a.accept(self.ev(m.EV_SUBMIT, 1, ()))  # ix(x) == n fails
        a.accept(self.ev(m.EV_SUBMIT, 0, ()))
        with self.assertRaises(m.Rejected):
            a.accept(self.act(m.ACT_START, (0, 5)))  # the job's second field is uses(node(0)) = 0
        with self.assertRaises(m.Rejected):
            a.accept(self.act(m.ACT_FINISH, 7))
        self.assertEqual(a.read("n"), 1)  # a rejected entry changes nothing

    def test_widths_are_checked_at_every_write(self):
        # §3.12.4: run is u16, the other Nats u32 ([widths] of mini's codegen.toml).
        m, a = self.m, self.acc
        with self.assertRaises(m.Overflow):
            a.read("tally").set(0, 2**32)
        a.read("tally").set(0, 2**32 - 1)
        with self.assertRaises(m.Overflow):
            m._fit(2**16, 0, 2**16 - 1, "run")
        self.assertIn("_fit(_n_run, 0, 65535, 'run')", pyback.emit(_mini()))

    def test_union_argument(self):
        m, a = self.m, self.acc
        a.accept(self.ev(m.EV_HALT, (1, 3)))  # halt(wfail(node(3)))
        self.assertEqual(a.read("why"), (1, 3))

    def test_walk_with_or(self):
        m = self.m
        st = m._Col(0)
        st.set(2, 1)
        self.assertTrue(m._m_anyBusy((0, 2), st))
        self.assertFalse(m._m_anyBusy((0, 1), st))
        self.assertFalse(m._m_anyBusy((), st))

    def test_the_module_has_no_recursion_and_imports_nothing(self):
        src = pyback.emit(_mini())
        # §3.12.1: array, heapq and collections only.
        imports = re.findall(r"(?m)^\s*(?:import|from)\s+(\w+)", src)
        self.assertLessEqual(set(imports), {"array", "heapq", "collections"})
        for fn in re.findall(r"(?m)^def (_m_\w+)\(", src):
            body = src.split("def %s(" % fn, 1)[1].split("\ndef ", 1)[0]
            self.assertNotIn(fn + "(", body, fn)


def _pinned() -> bool:
    return Toolchain().version() == PIN


def _mini_root() -> str:
    """A project holding the mini fixture."""
    d = tempfile.mkdtemp(prefix="naima-codegen-mini-")
    (Path(d) / "model").mkdir()
    for f in ("core.mcrl2", "codegen-init.mcrl2", "codegen.toml"):
        shutil.copyfile(FIX / "mini" / f, Path(d) / "model" / f)
    return d


def _cargo(d: Path, lib: str, main: str | None = None, name: str = "cases") -> subprocess.CompletedProcess:
    """Builds lib (and main, a binary) as a standalone crate; clippy without main."""
    from naima_codegen import rustback

    (d / "src").mkdir()
    extra = '\n[[bin]]\nname = "%s"\npath = "src/main.rs"\n' % name if main is not None else ""
    (d / "Cargo.toml").write_text(rustback.cargo_toml(CRATE, False) + extra + "\n[workspace]\n")
    (d / "src" / "lib.rs").write_text(lib)
    if main is not None:
        (d / "src" / "main.rs").write_text(main)
    shutil.copyfile(ROOT / "rust-toolchain.toml", d / "rust-toolchain.toml")
    cmd = ["cargo", "run", "--quiet", "--offline", "--bin", name] if main is not None else ["cargo", "clippy", "--offline", "--quiet"]
    return subprocess.run(cmd, cwd=d, capture_output=True, text=True, env=dict(os.environ, CARGO_TARGET_DIR=str(TARGET)))


class _Maps:
    """Every user map of a model against mCRL2's rewriter, in both backends."""

    CASES = 12  # per map

    @classmethod
    def setUpClass(cls):
        if not _pinned():
            raise unittest.SkipTest("the pinned mCRL2 toolset %s is not installed" % PIN)
        cls.root, cls.tmp = cls.project()
        cls.model = run(cls.root)
        cls.mod = _load(pyback.emit(cls.model))
        cls.types = Types(cls.model.lps, cls.model.config.id_sorts)
        cls.cases, cls.printed = _cases_and_mcrl2(cls.model, cls.mod, cls.types, cls.root, cls.CASES)

    @classmethod
    def tearDownClass(cls):
        if cls.tmp:
            shutil.rmtree(cls.root, ignore_errors=True)

    def _want(self, case, k):
        m, pyargs, pts, rsort, n, first = case
        return [to_python(parse_printed(lbl), rsort, self.types) for lbl in self.printed[first:first + n]]

    def test_every_map_with_arguments_is_tested(self):
        self.assertEqual({c[0].name for c in self.cases}, {m.name for m in self.model.user_maps if m.arity})

    def test_python_backend_agrees_with_the_mcrl2_rewriter(self):
        for case in self.cases:
            m, pyargs, pts, rsort, n, first = case
            got = self.mod.__dict__["_m_" + m.name](*[_copy(self.mod, a) for a in pyargs])
            got = [got.get(p) for _, p in pts] if pts else [got]
            self.assertEqual(got, self._want(case, first), "%s on %r" % (m.name, pyargs))

    def test_rust_crate_is_sans_io_and_passes_the_no_panic_lints(self):
        # §3.11.1: no I/O, threads, time, environment; §3.11.3: the no-panic lint set,
        # which the crate denies itself, checked by clippy.
        from naima_codegen import rustback

        files = rustback.emit(self.model)
        lib = files["src/lib.rs"]
        for bad in ("std::fs", "std::io", "std::net", "std::thread", "std::sync", "std::time", "std::env", "std::process",
                    "unsafe ", "extern crate"):
            self.assertNotIn(bad, lib)
        for lint in ("unwrap_used", "expect_used", "panic", "indexing_slicing", "arithmetic_side_effects"):
            self.assertIn("clippy::%s" % lint, lib.split("#![allow")[0])
        d = Path(tempfile.mkdtemp(prefix="naima-codegen-rs-"))
        try:
            r = _cargo(d, lib)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_rust_backend_agrees_with_the_mcrl2_rewriter(self):
        out = _run_rust_cases(self.model, self.types, self.cases)
        for case in self.cases:
            m, pyargs, pts, rsort, n, first = case
            got = [to_python(parse_printed(out[first + k]), rsort, self.types) for k in range(n)]
            self.assertEqual(got, self._want(case, first), "%s on %r" % (m.name, pyargs))


class MiniMaps(_Maps, unittest.TestCase):
    @staticmethod
    def project():
        return _mini_root(), True


def _moves_root() -> str:
    """The mini fixture with sets held as values and built from their old values: a walk
    that removes from its set accumulator, RQ's new value made by that walk from RQ, and
    maps on two sets (union, difference, intersection: rt's in-place forms)."""
    d = _mini_root()
    core, toml = Path(d) / "model" / "core.mcrl2", Path(d) / "model" / "codegen.toml"
    text = core.read_text()
    for old, new in (
            ("     tick: (St -> Nat) # St -> (St -> Nat);",
             "     tick: (St -> Nat) # St -> (St -> Nat);\n"
             "     drop: FSet(Node) # List(Node) -> FSet(Node);\n"
             "     join, less, meet: FSet(Node) # FSet(Node) -> FSet(Node);"),
            ("var  s: St;", "var  S, T: FSet(Node); s: St;"),
            ("     tick(t, s) = t[s -> t(s) + 1];",
             "     tick(t, s) = t[s -> t(s) + 1];\n"
             "     drop(S, []) = S;\n     drop(S, n |> l) = drop(S - {n}, l);\n"
             "     join(S, T) = S + T;\n     less(S, T) = S - T;\n     meet(S, T) = S * T;"),
            ("RQ = RQ + {x}, uses", "RQ = drop(RQ + {x}, l), uses")):
        assert old in text, old
        text = text.replace(old, new)
    core.write_text(text)
    toml.write_text(toml.read_text().replace('name = "RQ"\nrole = "frontier"\nrepr = "bitset"',
                                             'name = "RQ"\nrole = "frontier"'))
    return d


class MovedSets(_Maps, unittest.TestCase):
    """A set built from its old value moves the old value in (std::mem::take), never
    copies it; the maps on sets still agree with mCRL2's rewriter in both backends."""

    @staticmethod
    def project():
        return _moves_root(), True

    def test_old_sets_are_moved_not_cloned(self):
        from naima_codegen import rustback

        lib = rustback.emit(self.model)["src/lib.rs"]
        self.assertIn("break std::mem::take(&mut v_S);", lib)  # the walk's exit
        self.assertIn("rt::diff(std::mem::take(&mut v_S), ", lib)  # the walk's step
        self.assertIn("m_drop(rt::union(std::mem::take(&mut s.RQ), ", lib)  # the step's new RQ
        self.assertNotIn("s.RQ.clone()", lib)
        for op in ("union", "diff", "inter"):
            self.assertNotIn("pub fn %s<X: Ord + Clone>" % op, lib)


class ExampleMaps(_Maps, unittest.TestCase):
    @staticmethod
    def project():
        return str(EXAMPLE), False


def _copy(mod, a):
    """A stored function is written in place by the map: give each call its own."""
    if isinstance(a, mod._Col):
        c = mod._Col(a.d)
        c.m = dict(a.m)
        return c
    return a


def _cases_and_mcrl2(model, mod, types, root, per_map):
    """Seeded random calls of every user map and the values mCRL2 prints for them."""
    from codegen_values import sort_text

    gen = Gen(types, mod, seed=20261007)
    acts, seq, cases = {}, [], []
    maps = sorted((m for m in model.user_maps if m.arity), key=lambda m: m.name)
    for m in maps:
        for _ in range(per_map):
            args = [gen.value(s) for s in m.sort[1]]
            call = "%s(%s)" % (m.name, ", ".join(a for a, _ in args))
            res = types.resolve(m.sort[2])
            if res[0] == "->":
                pts = gen.points(res[1][0])
                terms = ["%s(%s)" % (call, p) for p, _ in pts]
                rsort = res[2]
            else:
                pts, terms, rsort = None, [call], res
            key = repr(rsort)
            if key not in acts:
                acts[key] = ("o%d" % len(acts), rsort)
            first = len(seq)
            for t in terms:
                seq.append("%s(%d, %s)" % (acts[key][0], len(seq), t))
            cases.append((m, [b for _, b in args], pts, rsort, len(terms), first))
    from naima_codegen.project import load

    spec = load(root).path(load(root).m("core.mcrl2")).read_text()
    # One state with a choice of every labelled value: linear to linearise.
    spec += "\nact %s;\ninit %s;\n" % (
        "; ".join("%s: Nat # %s" % (n, sort_text(s)) for n, s in acts.values()), " + ".join(seq))
    printed = _labels(spec)
    assert len(printed) == len(seq), (len(printed), len(seq))
    return cases, printed


def _rust_lit(v, s, types) -> str:
    """A Rust literal of the generated crate for the Python value v of sort s."""
    from naima_codegen.rustback import ident, type_name

    out: list = []
    stack = [(v, s, False)]
    while stack:
        x, srt, seen = stack.pop()
        srt = types.resolve(srt)
        k = types.kind(srt)
        if k == "bool":
            out.append("true" if x else "false")
            continue
        if k == "int":
            out.append("%di128" % x)
            continue
        if k in ("List", "FSet"):
            items = list(x) if k == "List" else sorted(x, key=repr)
            if not seen:
                stack.append((x, srt, True))
                for it in reversed(items):
                    stack.append((it, srt[1], False))
                continue
            vals = out[len(out) - len(items):] if items else []
            del out[len(out) - len(items):]
            out.append(("vec![%s]" if k == "List" else "BTreeSet::from([%s])") % ", ".join(vals))
            continue
        if k == "fun":
            entries = sorted(x.m.items(), key=repr)
            if not seen:
                stack.append((x, srt, True))
                stack.append((x.d, srt[2], False))
                for kk, vv in reversed(entries):
                    stack.append((vv, srt[2], False))
                    stack.append((kk, srt[1][0], False))
                continue
            n = 2 * len(entries) + 1
            vals = out[len(out) - n:]
            del out[len(out) - n:]
            d, rest = vals[-1], vals[:-1]
            pairs = ", ".join("(%s, %s)" % (rest[2 * i], rest[2 * i + 1]) for i in range(len(entries)))
            out.append("Fun::from_parts(%s, vec![%s])" % (d, pairs))
            continue
        name = srt[1]
        tn = type_name(name)
        shape = types.shape[name]
        if shape == "id":
            out.append("%s(%d)" % (tn, x))
            continue
        if shape == "enumeration":
            out.append("%s::%s" % (tn, ident(types.lps.structs[name].constructors[x][0])))
            continue
        ci = 0 if shape == "record" else x[0]
        c, fields, _ = types.lps.structs[name].constructors[ci]
        vals_in = list(x) if shape == "record" else list(x[1:])
        if not seen:
            stack.append((x, srt, True))
            for it, (_, fs) in reversed(list(zip(vals_in, fields))):
                stack.append((it, fs, False))
            continue
        n = len(fields)
        vals = out[len(out) - n:] if n else []
        del out[len(out) - n:]
        if shape == "record":
            out.append("(%s { %s })" % (tn, ", ".join("%s: %s" % (ident(p) if p else "f%d" % j, vv)
                                                       for j, ((p, _), vv) in enumerate(zip(fields, vals)))))
        else:
            out.append("%s::%s%s" % (tn, ident(c), "(%s)" % ", ".join(vals) if vals else ""))
    return out[0]


def _run_rust_cases(model, types, cases) -> dict:
    """Builds the generated crate with a program that prints every case's values; {index: text}."""
    from naima_codegen import rustback
    from naima_codegen.functions import is_fun

    em = rustback.Emitter(model)
    body = ["use std::collections::BTreeSet;", "use %s::*;" % CRATE, "", "#[allow(unused_mut, clippy::all)]", "fn main() {", "    let mut out = String::new();"]
    for m, pyargs, pts, rsort, n, first in cases:
        written = em.written(m.name)
        ret = model.fun_maps.get(m.name, ((), None))[1]
        lets, args = [], []
        for j, (v, s) in enumerate(zip(pyargs, m.sort[1])):
            lit = _rust_lit(v, s, types)
            if is_fun(model.lps, s):
                lets.append("let mut f%d = %s;" % (j, lit))
                args.append(("&mut f%d" if j in written else "&f%d") % j)
            else:
                args.append(lit)
        call = "m_%s(%s)" % (rustback.ident(m.name), ", ".join(args))
        body.append("    {")
        body += ["        " + x for x in lets]
        if ret is not None:
            body.append("        match %s {" % call)
            body.append("            Ok(()) => {")
            for k, (_, p) in enumerate(pts):
                body.append('                out.push_str("%d ");' % (first + k))
                body.append("                f%d.get(&%s).m2(&mut out);" % (ret, _rust_lit(p, types.resolve(m.sort[1][ret])[1][0], types)))
                body.append("                out.push('\\n');")
            body.append("            }")
        else:
            body.append("        match %s {" % call)
            body.append("            Ok(v) => {")
            body.append('                out.push_str("%d ");' % first)
            body.append("                v.m2(&mut out);")
            body.append("                out.push('\\n');")
            body.append("            }")
        body.append('            Err(e) => out.push_str(&format!("%d ERR {:?}\\n", e)),' % first)
        body.append("        }")
        body.append("    }")
    body += ['    print!("{}", out);', "}"]
    files = rustback.emit(model)
    d = Path(tempfile.mkdtemp(prefix="naima-codegen-rs-"))
    try:
        r = _cargo(d, files["src/lib.rs"], "\n".join(body) + "\n", "cases")
        if r.returncode:
            raise AssertionError(r.stderr[-4000:])
        out = {}
        for line in r.stdout.splitlines():
            k, _, v = line.partition(" ")
            out[int(k)] = v
        return out
    finally:
        shutil.rmtree(d, ignore_errors=True)


WALKS, STEPS = 40, 400


class ExampleTraces(unittest.TestCase):
    """Every entry of seeded random paths of the example's model systems
    (<generated>/model/systems/<group>-model.mcrl2, lps2lts) is accepted by the
    acceptor of both backends (§3.8.9, §3.9.8)."""

    @classmethod
    def setUpClass(cls):
        if not _pinned():
            raise unittest.SkipTest("the pinned mCRL2 toolset %s is not installed" % PIN)
        cls.model = run(EXAMPLE)
        cls.types = Types(cls.model.lps, cls.model.config.id_sorts)
        cls.mod = _load(pyback.emit(cls.model))
        cls.runs = [w for g in ("small", "flaky") for w in _group_walks(cls.model, cls.types, g)]

    def test_python_acceptor_accepts_every_trace(self):
        m = self.mod
        n = 0
        for w, (name, config, entries) in enumerate(self.runs):
            acc = m.Acceptor(m.Config(**config))
            for k, (side, label, code, args, _) in enumerate(entries):
                try:
                    acc.accept((side, (code,) + tuple(args)))
                except m.Rejected as e:
                    self.fail("%s walk %d entry %d %s%r: %r" % (name, w, k, label, args, e))
                n += 1
        self.assertGreater(n, 300)

    def _rust(self):
        if not hasattr(type(self), "rust_out"):
            type(self).rust_out = _run_rust_traces(self.model, self.types, self.runs)
        return type(self).rust_out

    def test_entries_encode_alike_in_both_backends_and_decode_back(self):
        m = self.mod
        lines = self._rust()
        k = 0
        for name, config, entries in self.runs:
            for side, label, code, args, _ in entries:
                e = (side, (code,) + tuple(args))
                b = m.encode(e)
                self.assertEqual(m.decode(b), e, label)
                _, _, _, hexs, rt, _ = lines[k].split(" ", 5)
                self.assertEqual((hexs, rt), (b.hex(), "true"), "%s %r" % (label, args))
                k += 1
        with self.assertRaises(m.DecodeError):
            m.decode(m.encode(e) + b"\x00")

    def test_rust_acceptor_accepts_every_trace(self):
        out = self._rust()
        self.assertEqual([x for x in out if not x.endswith(" ok")], [])
        self.assertEqual(len(out), sum(len(e) for _, _, e in self.runs))


def _w_config(text):
    """The configuration of an instance of the job queue: its number of workers."""
    return {"w": int(re.search(r"I_w\(@I\) = (\d+);", text).group(1))}


def _group_walks(model, types, group, example=EXAMPLE, config=_w_config):
    """[(instance, configuration, entries)]: seeded random paths of the group's model
    system, each the instance it picked and Core's entries (side, label, code, Python args, sorts)."""
    import random

    from naima_codegen.project import load

    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        shutil.copyfile(example / "generated" / "model" / "systems" / ("%s-model.mcrl2" % group), p / "m.mcrl2")
        for cmd in (["mcrl22lps", "--lin-method=regular", "-n", "m.mcrl2", "m.lps"], ["lps2lts", "--max=200000", "m.lps", "m.aut"]):
            r = subprocess.run(cmd, cwd=d, capture_output=True, text=True)
            if r.returncode:
                raise AssertionError(r.stderr[-2000:])
        edges: dict = {}
        for line in (p / "m.aut").read_text().splitlines()[1:]:
            mm = re.match(r'\((\d+),"(.*)",(\d+)\)$', line.strip())
            edges.setdefault(int(mm.group(1)), []).append((mm.group(2), int(mm.group(3))))
    inputs = sorted((a for a in model.actions if a.kind == "input"), key=lambda a: a.label)
    outputs = sorted((a for a in model.actions if a.kind == "output"), key=lambda a: a.label)
    rnd = random.Random(20261007)
    walks = []
    for _ in range(WALKS):
        s, entries, inst = 0, [], None
        for _ in range(STEPS):
            if s not in edges:
                break
            lbl, s = rnd.choice(edges[s])
            if lbl.startswith("pick("):
                inst = lbl[len("pick(i_"):-1]
                continue
            e = _entry(unbind_ids(lbl, model, load(example).cfg.systems["ids"]), inputs, outputs, types)
            if e is not None:
                entries.append(e)
        text = (example / "model" / "instances" / ("%s.mcrl2" % inst)).read_text()
        walks.append((inst, config(text), entries))
    return walks


def _entry(lbl, inputs, outputs, types):
    head, _, rest = lbl.partition("(")
    tree = parse_printed(lbl)
    if tree[0] != "app":
        return None
    args = tree[2]
    for side, labels in ((0, inputs), (1, outputs)):
        for code, a in enumerate(labels):
            if a.label == "c_" + head and len(a.sorts) == len(args):
                return (side, a.label, code, [to_python(x, s, types) for x, s in zip(args, a.sorts)], a.sorts)
    return None


def _run_rust_traces(model, types, runs) -> list:
    from naima_codegen import rustback

    body = ["use std::collections::BTreeSet;", "use %s::*;" % CRATE, "", "#[allow(unused_mut, clippy::all)]",
            "fn main() {", "    let mut out = String::new();"]
    sorts = dict(model.lps.params)
    for w, (name, config, entries) in enumerate(runs):
        cfg = ", ".join("%s: %s" % (rustback.ident(k), _rust_lit(v, sorts[k], types)) for k, v in config.items())
        body.append("    {")
        body.append("        let mut a = Acceptor::new(Config { %s }).unwrap();" % cfg)
        for k, (side, label, code, args, asorts) in enumerate(entries):
            enum = "Event" if side == 0 else "Action"
            lits = ", ".join(_rust_lit(v, s, types) for v, s in zip(args, asorts))
            entry = "Entry::%s(%s::%s%s)" % (enum, enum, rustback.camel(label), "(%s)" % lits if asorts else "")
            body.append("        let e = %s;" % entry)
            body.append("        let mut b = Vec::new();")
            body.append("        encode(&e, &mut b);")
            body.append("        let hex: String = b.iter().map(|x| format!(\"{:02x}\", x)).collect();")
            body.append('        out.push_str(&format!("%s %d %d {} {} {:?}\\n", hex, decode(&b) == Ok(e.clone()), a.accept(&e).map(|_| "ok")));'
                        % (name, w, k))
        body.append("    }")
    body += ['    print!("{}", out.replace("Ok(\\"ok\\")", "ok"));', "}"]
    files = rustback.emit(model)
    d = Path(tempfile.mkdtemp(prefix="naima-codegen-rs-"))
    try:
        r = _cargo(d, files["src/lib.rs"], "\n".join(body) + "\n", "traces")
        if r.returncode:
            raise AssertionError(r.stderr[-4000:])
        return r.stdout.splitlines()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _labels(spec: str) -> list:
    """The values printed by `o<k>(i, value)` transitions, by i (lps2lts)."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        (p / "m.mcrl2").write_text(spec)
        r = subprocess.run(["mcrl22lps", "--lin-method=regular", "-n", "m.mcrl2", "m.lps"], cwd=d, capture_output=True, text=True)
        if r.returncode:
            raise AssertionError(r.stderr[-2000:])
        r = subprocess.run(["lps2lts", "m.lps", "m.aut"], cwd=d, capture_output=True, text=True)
        if r.returncode:
            raise AssertionError(r.stderr[-2000:])
        out = {}
        for line in (p / "m.aut").read_text().splitlines()[1:]:
            m = re.match(r'\(\s*\d+\s*,\s*"o\d+\((\d+),\s*(.*)\)"\s*,\s*\d+\s*\)$', line.strip())
            if m is None and "Terminate" in line:
                continue
            if m is None:
                raise AssertionError("unexpected transition %r" % line)
            out[int(m.group(1))] = m.group(2)
        return [out[k] for k in range(len(out))]


if __name__ == "__main__":
    unittest.main()
