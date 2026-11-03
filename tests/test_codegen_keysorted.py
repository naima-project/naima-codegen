"""Lists kept sorted by a key (docs/spec.md §3.10.7), on the fixture model
tests/fixtures/keyqueue: the insert ordered by a key and the first-occurrence
removal are recognised from their equations; the Rust code changes the entry in
place and gives the model's walk (every step of a random record is accepted, in a
debug build); and the cost of queueing n elements grows linearly with n (4,000 and 40,000 elements: the
walk that copies the list made it quadratic, bugs in VoxLogicA's for-expansion).

The end-to-end tests need mcrl22lps and cargo, and skip, saying so, without them.
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
FIXTURE = ROOT / "tests" / "fixtures" / "keyqueue"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from naima_codegen.expr import App, Op, Var  # noqa: E402
from naima_codegen.lps import Equation, Lps, MapDecl, Struct  # noqa: E402
from naima_codegen.sortedlists import first_removes, key_inserts  # noqa: E402

NAT = ("id", "Nat")
BOOL = ("id", "Bool")
NODE = ("id", "Node")
LN = ("List", NODE)
KEY = ("->", (NODE,), NAT)
KINS = ("->", (NODE, LN, KEY), LN)
REM = ("->", (LN, NODE), LN)
TRUE = Op("true", BOOL)
NIL = Op("[]", LN)


def app(h, *a):
    return App(Op(h, ("->", (), BOOL)) if isinstance(h, str) else h, tuple(a))


def cons(a, b):
    return app("|>", a, b)


def kins_equations(less="<"):
    u, n, l, f = Var("u", NODE), Var("n", NODE), Var("l", LN), Var("f", KEY)
    g = Op("kins", KINS)
    return [Equation((), TRUE, App(g, (u, NIL, f)), cons(u, NIL)),
            Equation((), TRUE, App(g, (u, cons(n, l), f)),
                     app("if", app(less, App(f, (u,)), App(f, (n,))), cons(u, cons(n, l)), cons(n, App(g, (u, l, f)))))]


def rem_equations(keep_first=True):
    u, n, l = Var("u", NODE), Var("n", NODE), Var("l", LN)
    r = Op("rem", REM)
    rest = l if keep_first else App(r, (l, u))
    return [Equation((), TRUE, App(r, (NIL, u)), NIL),
            Equation((), TRUE, App(r, (cons(n, l), u)), app("if", app("==", n, u), rest, cons(n, App(r, (l, u)))))]


def model(eqs):
    return Lps(structs={"Node": Struct("Node", [("node", [("ix", NAT)], "")])},
               maps=[MapDecl("kins", KINS), MapDecl("rem", REM)], equations=eqs,
               params=[], summands=[], init=[])


class Recognised(unittest.TestCase):
    def test_the_key_insert_and_the_removal_are_recognised(self):
        x = model(kins_equations() + rem_equations())
        self.assertEqual((key_inserts(x), first_removes(x)), ({"kins"}, {"rem"}))

    def test_other_orders_and_removals_are_not(self):
        x = model(kins_equations(less="<=") + rem_equations(keep_first=False))
        self.assertEqual((key_inserts(x), first_removes(x)), (set(), set()))


MAIN = r"""
use keyqueue::*;
use std::io::BufRead;

fn ok(a: &mut Acceptor, e: Entry) {
    if let Err(r) = a.accept(&e) {
        panic!("{e:?}: {r:?}");
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let mut a = Acceptor::new(Config {}).unwrap();
    if args[1] == "time" {
        // n elements queued in key order, as a loop's expansion queues them, then taken
        let n: u32 = args[2].parse().unwrap();
        let t = std::time::Instant::now();
        for i in 0..n {
            ok(&mut a, Entry::Event(Event::Put(Item(i), i128::from(i))));
            ok(&mut a, Entry::Action(Action::Queue(Item(i))));
        }
        let q = t.elapsed().as_secs_f64();
        for i in 0..n {
            ok(&mut a, Entry::Event(Event::Take(Item(i))));
        }
        println!("{} {}", q, t.elapsed().as_secs_f64() - q);
        return;
    }
    for line in std::io::stdin().lock().lines() {
        let line = line.unwrap();
        let f: Vec<&str> = line.split(' ').collect();
        let i = Item(f[1].parse().unwrap());
        match f[0] {
            "P" => ok(&mut a, Entry::Event(Event::Put(i, f[2].parse().unwrap()))),
            "Q" => ok(&mut a, Entry::Action(Action::Queue(i))),
            "T" => ok(&mut a, Entry::Event(Event::Take(i))),
            _ => ok(&mut a, Entry::Event(Event::Drop(i))),
        }
    }
    println!("accepted");
}
"""


def record(seed: int, n: int = 300) -> list:
    """A random record of the fixture: keys with ties, queued in random order, takes
    and drops interleaved; the take is the head the model's walk gives."""
    r = random.Random(seed)
    key, q, out, fresh = {}, [], [], []
    for i in range(n):
        key[i] = r.randrange(n // 4)
        out.append("P %d %d" % (i, key[i]))
        fresh.append(i)
    r.shuffle(fresh)
    for i in fresh:
        out.append("Q %d" % i)
        p = next((j for j, x in enumerate(q) if key[i] < key[x]), len(q))
        q.insert(p, i)
        c = r.random()
        if c < 0.2 and q:
            out.append("T %d" % q.pop(0))
        elif c < 0.3 and q:
            out.append("D %d" % q.pop(r.randrange(len(q))))
    while q:
        out.append("T %d" % q.pop(0))
    return out


@unittest.skipIf(shutil.which("mcrl22lps") is None or shutil.which("cargo") is None, "needs mcrl22lps and cargo")
class EndToEnd(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="naima-codegen-keyqueue-"))
        proj = cls.dir / "proj"
        shutil.copytree(FIXTURE, proj)
        r = subprocess.run([sys.executable, "-m", "naima_codegen", "generate", "--project", str(proj)],
                           capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(ROOT)))
        if r.returncode:
            raise AssertionError(r.stdout[-2000:] + r.stderr[-2000:])
        crate = proj / "generated" / "rust" / "keyqueue"
        cls.lib = (crate / "src" / "lib.rs").read_text()
        d = cls.dir / "main"
        (d / "src").mkdir(parents=True)
        (d / "Cargo.toml").write_text('[package]\nname = "kq-main"\nversion = "0.0.0"\nedition = "2024"\npublish = false\n\n'
                                      '[dependencies]\nkeyqueue = { path = "%s" }\n\n[workspace]\n' % crate)
        (d / "src" / "main.rs").write_text(MAIN)
        shutil.copyfile(ROOT / "rust-toolchain.toml", d / "rust-toolchain.toml")
        cls.env = dict(os.environ, CARGO_TARGET_DIR=os.environ.get("CARGO_TARGET_DIR", str(ROOT / "target" / "codegen-tests")))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def run_main(self, args, release, stdin=None):
        cmd = ["cargo", "run", "--quiet", "--offline"] + (["--release"] if release else []) + ["--"] + args
        r = subprocess.run(cmd, cwd=self.dir / "main", input=stdin, capture_output=True, text=True, env=self.env)
        if r.returncode:
            raise AssertionError(r.stderr[-4000:])
        return r.stdout

    def test_the_entry_is_changed_in_place(self):
        for op in ("rt::ins_by_key(s.q.slot(", "rt::pop_first(s.q.slot(", "rt::rem_first(s.q.slot("):
            self.assertIn(op, self.lib)
        self.assertNotIn("m_k_ins(", self.lib.split("pub fn m_k_ins(")[1].split("\n}\n", 1)[1])

    def test_a_random_record_is_accepted_in_debug(self):
        for seed in range(5):
            with self.subTest(seed=seed):
                self.assertEqual(self.run_main(["check"], False, "\n".join(record(seed)) + "\n").split(), ["accepted"])

    def test_queueing_cost_grows_linearly(self):
        # The best of three runs at each size; linear is a ratio of 10, quadratic of 100. Taking
        # the head shifts the rest of the entry (Vec::remove(0)), O(k) per take, so only the
        # queueing is held to linear (measured, release: taking 4,000 0.6 ms, 40,000 0.10 s,
        # 200,000 2.7 s).
        t = {n: min(float(self.run_main(["time", str(n)], True).split()[0]) for _ in range(3)) for n in (4000, 40000)}
        self.assertLess(t[40000] / t[4000], 25, t)


if __name__ == "__main__":
    unittest.main()
