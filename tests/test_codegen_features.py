"""Rewriter normal forms, list order and the per-id growing list, each against mCRL2
in every backend (docs/spec.md §3.2.6, §3.7.6, §3.10.3).

- rewriter normal forms (§3.2.6): a Core whose next-state assignments are accepted
  expressions over variables whose normal forms use the toolset's internal operators
  of numbers; the acceptor of every backend, on seeded random events, holds the values
  mCRL2's rewriter gives the expressions as written;
- list order (§3.7.6): `<` on lists of numbers, compared lexicographically with a proper
  prefix first, in mCRL2, Python, Rust and TypeScript, on seeded random pairs that
  share prefixes;
- the per-id growing list (§3.10.3, §3.10.1): seeded random sequences of appends,
  drains, heads, lengths, emptiness tests, reads and writes at several ids, through the
  Rust `Adj` and through the Python backend's representation, against mCRL2's lists.

The upkeep of newest and oldest indexes written at several ids in one summand (§3.7.4)
and a map keyed by a record holding a list (§3.13.1) run in examples/waits, through the
state-space and cross-backend tests of test_codegen_typescript.py.
"""

import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import codegen_ts as ts  # noqa: E402
from codegen_values import parse_printed, to_python  # noqa: E402
from naima_codegen import pyback, rustback, toolchain, tsback  # noqa: E402
from naima_codegen.expr import App, Op, walk  # noqa: E402
from naima_codegen.frontend import run  # noqa: E402
from naima_codegen.lower import Types  # noqa: E402
from naima_codegen.toolchain import Toolchain  # noqa: E402
from test_codegen_python import _cargo, _labels, _load  # noqa: E402

PIN = "202607.0"

# Accepted expressions over n, m: Nat, p, q: Pos, i: Int, l: List(Nat), with their sorts, chosen so
# that their normal forms use every internal operator of §3.2.6.
FORMS = [
    ("Nat", "#(n |> l)"),
    ("Nat", "Pos2Nat(succ(n))"),
    ("Nat", "Pos2Nat(succ(p))"),
    ("Nat", "Int2Nat(p - 1)"),
    ("Nat", "Int2Nat(Pos2Nat(p) - 1)"),
    ("Nat", "Int2Nat(succ(p) - 2)"),
    ("Nat", "max(m, Pos2Nat(succ(n)))"),
    ("Nat", "max(m, #(n |> l))"),
    ("Pos", "succ(p)"),
    ("Pos", "Nat2Pos(Pos2Nat(succ(n)))"),
    ("Int", "pred(n)"),
    ("Int", "pred(m) + i"),
    ("Int", "succ(i)"),
    ("Bool", "succ(p) < q"),
    ("Bool", "p <= succ(q)"),
    ("Bool", "n <= Pos2Nat(succ(m))"),
    ("Bool", "Pos2Nat(succ(n)) < m"),
    ("Bool", "#(n |> l) <= m"),
    ("Bool", "Pos2Nat(succ(n)) == m"),
    ("Bool", "succ(p) == q"),
]
INTERNAL = {"@succ_nat", "@succ_pos", "@natpred", "@pospred", "@monus", "@equals_zero", "@equals_one"}
LABEL = {"Nat": "o0", "Pos": "o1", "Int": "o2", "Bool": "o3"}
INIT = {"Nat": "0", "Pos": "1", "Int": "0", "Bool": "false"}
SORT = {"Nat": ("id", "Nat"), "Pos": ("id", "Pos"), "Int": ("id", "Int"), "Bool": ("id", "Bool")}


def _pinned() -> bool:
    return Toolchain().version() == PIN


def _forms_root() -> Path:
    """A project whose input summand assigns every expression of FORMS to a parameter (which an
    output reads, so that linearisation keeps the assignments), and whose map `before` is `<` on
    lists."""
    d = Path(tempfile.mkdtemp(prefix="naima-codegen-forms-"))
    (d / "model").mkdir()
    params = ", ".join("r%d: %s" % (k, s) for k, (s, _) in enumerate(FORMS))
    assigns = ", ".join("r%d = %s" % (k, e) for k, (_, e) in enumerate(FORMS))
    (d / "model" / "core.mcrl2").write_text(
        "map  before: List(Nat) # List(Nat) -> Bool;\nvar  a, b: List(Nat);\neqn  before(a, b) = a < b;\n\n"
        "act  c_set: Nat # Nat # Pos # Pos # Int # List(Nat);\n     c_get: %s;\n\n"
        "proc Core(%s) =\n    sum n, m: Nat, p, q: Pos, i: Int, l: List(Nat) . true ->\n"
        "      c_set(n, m, p, q, i, l) . Core(%s)\n  + true -> c_get(%s) . Core()\n  + delta;\n"
        % (" # ".join(s for s, _ in FORMS), params, assigns, ", ".join("r%d" % k for k in range(len(FORMS)))))
    (d / "model" / "codegen-init.mcrl2").write_text("init Core(%s);\n" % ", ".join(INIT[s] for s, _ in FORMS))
    (d / "codegen.toml").write_text(
        'mcrl2_version = "%s"\nid_sorts = []\n\n[project]\nbackends = ["rust", "python", "typescript"]\n'
        'rust_crate = "forms"\npython_package = "forms"\ntypescript_module = "forms"\n\n' % PIN
        + "".join('[[parameter]]\nname = "r%d"\nrole = "scalar"\n\n' % k for k in range(len(FORMS)))
        + '[[action]]\nlabel = "c_set"\nkind = "input"\nargs = ["n", "m", "p", "q", "i", "l"]\n\n'
        + '[[action]]\nlabel = "c_get"\nkind = "output"\nargs = [%s]\n' % ", ".join('"r%d"' % k for k in range(len(FORMS))))
    return d


def _mcrl2_list(l) -> str:
    return "[%s]" % ", ".join(str(x) for x in l)


def _rust_list(l) -> str:
    return "vec![%s]" % ", ".join("%di128" % x for x in l)


def _list_pairs(rnd, n):
    """Pairs of lists of numbers, many sharing a prefix (one a prefix of the other, or equal heads)."""
    out = []
    for _ in range(n):
        common = [rnd.randrange(3) for _ in range(rnd.randrange(3))]
        a = common + [rnd.randrange(3) for _ in range(rnd.randrange(3))]
        b = common + [rnd.randrange(3) for _ in range(rnd.randrange(3))]
        out.append((a, b))
    return out + [([], []), ([], [0]), ([1], [1, 0]), ([1, 0], [2]), ([0, 5], [0, 5])]


class FormsAndListOrder(unittest.TestCase):
    """§3.2.6 and §3.7.6 in the three backends."""

    CASES = 40

    @classmethod
    def setUpClass(cls):
        if not _pinned():
            raise unittest.SkipTest("the pinned mCRL2 toolset %s is not installed" % PIN)
        cls.root = _forms_root()
        # Linearised with rewriting on, as an LPS may be (§3.2.6): the generator's own -o
        # (§3.2.3) leaves no normal forms to translate.
        with mock.patch.object(toolchain, "LINEARISE", [x for x in toolchain.LINEARISE if x != "-o"]):
            cls.model = run(cls.root)
        cls.types = Types(cls.model.lps, cls.model.config.id_sorts)
        rnd = random.Random(20261101)
        cls.events = [(rnd.randrange(4), rnd.randrange(4), 1 + rnd.randrange(3), 1 + rnd.randrange(3), rnd.randrange(-2, 3),
                       tuple(rnd.randrange(4) for _ in range(rnd.randrange(3)))) for _ in range(cls.CASES)]
        cls.pairs = _list_pairs(rnd, 60)
        # The values mCRL2's rewriter gives the expressions as written, on typed variables, and the
        # list order.
        k = len(FORMS)
        first = len(cls.events) * k
        spec = ("act o0: Nat # Nat; o1: Nat # Pos; o2: Nat # Int; o3: Nat # Bool;\n"
                "proc E(c: Nat, n, m: Nat, p, q: Pos, i: Int, l: List(Nat)) =\n    %s;\n" % "\n  + ".join(
                    "%s(c * %d + %d, %s) . delta" % (LABEL[s], k, j, e) for j, (s, e) in enumerate(FORMS)))
        calls = ["E(%d, %d, %d, %d, %d, %d, %s)" % ((c,) + ev[:5] + (_mcrl2_list(ev[5]),)) for c, ev in enumerate(cls.events)]
        calls += ["o3(%d, %s < %s) . delta" % (first + j, _mcrl2_list(a), _mcrl2_list(b)) for j, (a, b) in enumerate(cls.pairs)]
        printed = _labels(spec + "init %s;\n" % " + ".join(calls))
        cls.want = [[to_python(parse_printed(printed[c * k + j]), SORT[s], cls.types) for j, (s, _) in enumerate(FORMS)]
                    for c in range(len(cls.events))]
        cls.want_order = [to_python(parse_printed(x), SORT["Bool"], cls.types) for x in printed[first:]]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_the_normal_forms_use_every_internal_operator(self):
        used = set()
        for s in self.model.lps.summands:
            for _, e in s.assigns:
                used |= {x.head.name for x in walk(e) if isinstance(x, App) and isinstance(x.head, Op) and x.head.name.startswith("@")}
        self.assertLessEqual(INTERNAL, used)
        self.assertLessEqual(used, INTERNAL | {"@cInt", "@cNat", "@cNeg"})

    def test_python_holds_the_rewriters_values(self):
        m = _load(pyback.emit(self.model))
        for ev, want in zip(self.events, self.want):
            acc = m.Acceptor(m.Config())
            acc.accept((0, (m.EV_SET,) + ev))
            self.assertEqual([acc.read("r%d" % k) for k in range(len(FORMS))], want, "event %r" % (ev,))
        self.assertEqual([m._m_before(tuple(a), tuple(b)) for a, b in self.pairs], self.want_order)

    def test_rust_holds_the_rewriters_values(self):
        body = ["use generated_under_test::*;", "", "#[allow(clippy::all)]", "fn main() {", "    let mut out = String::new();"]
        for n, m, p, q, i, l in self.events:
            body += ["    {", "        let mut a = Acceptor::new(Config {}).unwrap();",
                     "        a.accept(&Entry::Event(Event::Set(%di128, %di128, %di128, %di128, %di128, %s))).unwrap();"
                     % (n, m, p, q, i, _rust_list(l)), "        let s = a.state();"]
            for k in range(len(FORMS)):
                body += ["        s.r%d.m2(&mut out);" % k, "        out.push(' ');"]
            body += ["        out.push('\\n');", "    }"]
        for a, b in self.pairs:
            body.append('    out.push_str(&format!("{}\\n", m_before(%s, %s).unwrap()));' % (_rust_list(a), _rust_list(b)))
        body += ['    print!("{}", out);', "}"]
        d = Path(tempfile.mkdtemp(prefix="naima-codegen-rs-"))
        try:
            r = _cargo(d, rustback.emit(self.model)["src/lib.rs"], "\n".join(body) + "\n", "forms")
            self.assertEqual(r.returncode, 0, r.stderr[-4000:])
        finally:
            shutil.rmtree(d, ignore_errors=True)
        lines = r.stdout.splitlines()
        for ev, line, want in zip(self.events, lines, self.want):
            got = [to_python(parse_printed(x), SORT[s], self.types) for x, (s, _) in zip(line.split(), FORMS)]
            self.assertEqual(got, want, "event %r" % (ev,))
        self.assertEqual([x == "true" for x in lines[len(self.events):]], self.want_order)

    def test_typescript_holds_the_rewriters_values(self):
        if ts.deno() is None:
            self.skipTest("deno is not installed")
        inputs = [a for a in self.model.actions if a.kind == "input"]
        runs = [{"config": {}, "entries": [ts.ts_entry(0, (0,) + ev, inputs, [], self.types)]} for ev in self.events]
        cases = [{"map": "before", "args": [list(a), list(b)], "points": None} for a, b in self.pairs]
        with tempfile.TemporaryDirectory(prefix="naima-ts-") as d:
            mod = Path(d) / "forms.ts"
            mod.write_text(tsback.emit(self.model, None, "forms")["forms.ts"])
            states = [json.loads(x) for x in ts.run_json("accept.ts", runs, mod).splitlines()]
            order = [json.loads(x) for x in ts.run_json("maps.ts", cases, mod).splitlines()]
        for ev, st, want in zip(self.events, states, self.want):
            self.assertEqual([st["r%d" % k] for k in range(len(FORMS))], want, "event %r" % (ev,))
        self.assertEqual([x[0] for x in order], self.want_order)


# ------------------------------------------------------------------ the per-id growing list (§3.10.3)

OPERATIONS, LENGTH, IDS = 10000, 200, 4

ADJ_SPEC = """sort Op = struct push(pi: Nat, pv: Nat)?is_push | pop(qi: Nat)?is_pop | hd(hi: Nat)?is_hd | ln(li: Nat)?is_ln
                | em(ei: Nat)?is_em | rd(ri: Nat)?is_rd | wr(wi: Nat, wl: List(Nat))?is_wr;
act step, undef: Nat # Nat; ov: Nat # Nat # Nat; ob: Nat # Nat # Bool; ol: Nat # Nat # List(Nat);
proc P(s: Nat, f: Nat -> List(Nat), ops: List(Op), k: Nat) =
    (ops != [] && is_push(head(ops))) -> step(s, k) . P(f = f[pi(head(ops)) -> f(pi(head(ops))) <| pv(head(ops))], ops = tail(ops), k = k + 1)
  + (ops != [] && is_pop(head(ops)) && f(qi(head(ops))) != []) -> step(s, k) . P(f = f[qi(head(ops)) -> tail(f(qi(head(ops))))], ops = tail(ops), k = k + 1)
  + (ops != [] && is_pop(head(ops)) && f(qi(head(ops))) == []) -> undef(s, k) . delta
  + (ops != [] && is_hd(head(ops)) && f(hi(head(ops))) != []) -> ov(s, k, head(f(hi(head(ops))))) . P(ops = tail(ops), k = k + 1)
  + (ops != [] && is_hd(head(ops)) && f(hi(head(ops))) == []) -> undef(s, k) . delta
  + (ops != [] && is_ln(head(ops))) -> ov(s, k, #f(li(head(ops)))) . P(ops = tail(ops), k = k + 1)
  + (ops != [] && is_em(head(ops))) -> ob(s, k, f(ei(head(ops))) == []) . P(ops = tail(ops), k = k + 1)
  + (ops != [] && is_rd(head(ops))) -> ol(s, k, f(ri(head(ops)))) . P(ops = tail(ops), k = k + 1)
  + (ops != [] && is_wr(head(ops))) -> step(s, k) . P(f = f[wi(head(ops)) -> wl(head(ops))], ops = tail(ops), k = k + 1);
"""


def _adj_sequences(rnd):
    """(default, ops): sequences of up to LENGTH operations, OPERATIONS in all, each on a column
    whose default is [] or [5]; a drain or head of an empty list happens rarely, and ends the
    sequence there."""
    out = []
    while sum(len(ops) for _, ops in out) < OPERATIONS:
        d = [] if len(out) % 2 == 0 else [5]
        lens = [len(d)] * IDS
        ops = []
        while len(ops) < LENGTH:
            i = rnd.randrange(IDS)
            r = rnd.random()
            if r < 0.3:
                ops.append(("push", i, rnd.randrange(10)))
                lens[i] += 1
            elif r < 0.55:
                if lens[i] == 0 and rnd.random() < 0.97:
                    continue
                ops.append(("pop", i))
                lens[i] -= 1
                if lens[i] < 0:
                    break
            elif r < 0.65:
                if lens[i] == 0 and rnd.random() < 0.97:
                    continue
                ops.append(("hd", i))
                if lens[i] == 0:
                    break
            elif r < 0.73:
                ops.append(("ln", i))
            elif r < 0.81:
                ops.append(("em", i))
            elif r < 0.91:
                ops.append(("rd", i))
            else:
                l = [rnd.randrange(10) for _ in range(rnd.randrange(4))]
                ops.append(("wr", i, l))
                lens[i] = len(l)
        out.append((d, ops))
    return out


def _mcrl2_op(op) -> str:
    if op[0] == "push":
        return "push(%d, %d)" % (op[1], op[2])
    if op[0] == "wr":
        return "wr(%d, %s)" % (op[1], _mcrl2_list(op[2]))
    return "%s(%d)" % op


class AdjConformance(unittest.TestCase):
    """§3.10.1, §3.10.3: the per-id growing list against mCRL2's lists, at least 10,000
    operations in sequences of up to 200."""

    @classmethod
    def setUpClass(cls):
        if not _pinned():
            raise unittest.SkipTest("the pinned mCRL2 toolset %s is not installed" % PIN)
        cls.seqs = _adj_sequences(random.Random(20261102))
        spec = ADJ_SPEC + "init %s;\n" % " + ".join(
            "P(%d, lambda i: Nat . %s, [%s], 0)" % (s, _mcrl2_list(d), ", ".join(_mcrl2_op(o) for o in ops))
            for s, (d, ops) in enumerate(cls.seqs))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "m.mcrl2").write_text(spec)
            for cmd in (["mcrl22lps", "-q", "m.mcrl2", "m.lps"], ["lps2lts", "-q", "m.lps", "m.aut"]):
                r = subprocess.run(cmd, cwd=d, capture_output=True, text=True)
                if r.returncode:
                    raise AssertionError(r.stderr[-2000:])
            labels = re.findall(r'"(\w+\(\d+, \d+(?:, .*?)?\))"', (p / "m.aut").read_text())
        key = re.compile(r"\w+\((\d+), (\d+)")
        cls.want = sorted(labels, key=lambda x: tuple(int(g) for g in key.match(x).groups()))

    def test_enough_operations(self):
        self.assertGreaterEqual(sum(len(ops) for _, ops in self.seqs), OPERATIONS)

    def test_python_representation(self):
        """The Python backend's representation (§3.12.3): a stored function of tuples, with the
        generated code's operations on them."""
        from test_codegen_python import _mini

        m = _load(pyback.emit(_mini()))
        got = []
        for s, (d, ops) in enumerate(self.seqs):
            f = m._Col(tuple(d))
            for k, op in enumerate(ops):
                i = op[1]
                try:
                    if op[0] == "push":
                        f.set(i, f.get(i) + (op[2],))
                        got.append("step(%d, %d)" % (s, k))
                    elif op[0] == "pop":
                        f.set(i, m._tail(f.get(i)))
                        got.append("step(%d, %d)" % (s, k))
                    elif op[0] == "hd":
                        got.append("ov(%d, %d, %d)" % (s, k, m._head(f.get(i))))
                    elif op[0] == "ln":
                        got.append("ov(%d, %d, %d)" % (s, k, len(f.get(i))))
                    elif op[0] == "em":
                        got.append("ob(%d, %d, %s)" % (s, k, "true" if f.get(i) == () else "false"))
                    elif op[0] == "rd":
                        got.append("ol(%d, %d, %s)" % (s, k, _mcrl2_list(f.get(i))))
                    else:
                        f.set(i, tuple(op[2]))
                        got.append("step(%d, %d)" % (s, k))
                except m.Undefined:
                    got.append("undef(%d, %d)" % (s, k))
                    break
        self.assertEqual(got, self.want)

    def test_rust_adj(self):
        from test_codegen_python import _mini

        body = ["use generated_under_test::*;", "", "#[allow(clippy::all)]", "fn main() {", "    let mut out = String::new();"]
        for s, (d, ops) in enumerate(self.seqs):
            body += ["    'seq%d: {" % s, "        let mut f: Adj<i128> = Adj::new(%s);" % _rust_list(d)]
            for k, op in enumerate(ops):
                i = op[1]
                step = 'out.push_str("step(%d, %d)\\n");' % (s, k)
                undef = 'out.push_str("undef(%d, %d)\\n"); break \'seq%d;' % (s, k, s)
                if op[0] in ("push", "pop", "wr"):
                    if op[0] == "push":
                        a = "AdjOp::Push(%d, %di128)" % (i, op[2])
                    elif op[0] == "pop":
                        a = "AdjOp::Pop(%d)" % i
                    else:
                        a = "AdjOp::Set(%d, %s)" % (i, _rust_list(op[2]))
                    body.append("        if f.apply(%s).is_ok() { %s } else { %s }" % (a, step, undef))
                elif op[0] == "hd":
                    body.append('        match f.head(%d) { Ok(v) => out.push_str(&format!("ov(%d, %d, {})\\n", v)), Err(_) => { %s } }'
                                % (i, s, k, undef))
                elif op[0] == "ln":
                    body.append('        out.push_str(&format!("ov(%d, %d, {})\\n", f.len(%d)));' % (s, k, i))
                elif op[0] == "em":
                    body.append('        out.push_str(&format!("ob(%d, %d, {})\\n", f.is_empty(%d)));' % (s, k, i))
                else:
                    body += ["        {", "            let mut t = String::new();", "            f.to_vec(%d).m2(&mut t);" % i,
                             '            out.push_str(&format!("ol(%d, %d, {})\\n", t));' % (s, k), "        }"]
            body.append("    }")
        body += ['    print!("{}", out);', "}"]
        d = Path(tempfile.mkdtemp(prefix="naima-codegen-rs-"))
        try:
            r = _cargo(d, rustback.emit(_mini())["src/lib.rs"], "\n".join(body) + "\n", "adj")
            self.assertEqual(r.returncode, 0, r.stderr[-4000:])
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(r.stdout.splitlines(), self.want)


if __name__ == "__main__":
    unittest.main()
