"""Sorted per-id lists (docs/spec.md §3.10.6): the ordered insert and merge are
recognised from their equations, a column is sorted only when every summand keeps it
so, and the in-place insert gives the walk's result on sorted lists."""

import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from naima_codegen.expr import App, Bind, Op, Var  # noqa: E402
from naima_codegen.lps import Equation, Lps, MapDecl, Struct, Summand  # noqa: E402
from naima_codegen.sortedlists import Sorted  # noqa: E402

NAT = ("id", "Nat")
BOOL = ("id", "Bool")
NODE = ("id", "Node")
LN = ("List", NODE)
COL = ("->", (NODE,), LN)
INS = ("->", (NODE, LN), LN)
MERGE = ("->", (LN, LN), LN)


def op(name, sort=None):
    return Op(name, sort or ("->", (), BOOL))


def app(head, *args):
    return App(op(head) if isinstance(head, str) else head, tuple(args))


def v(name, sort=NODE):
    return Var(name, sort)


NIL = Op("[]", LN)
TRUE = Op("true", BOOL)


def cons(a, b):
    return app("|>", a, b)


def ins_equations(name="ins", less="<"):
    u, n, l = v("u"), v("n"), v("l", LN)
    f = Op(name, INS)
    return [Equation((), TRUE, App(f, (u, NIL)), cons(u, NIL)),
            Equation((), TRUE, App(f, (u, cons(n, l))),
                     app("if", app("==", u, n), cons(n, l),
                         app("if", app(less, app("ix", u), app("ix", n)), cons(u, cons(n, l)),
                             cons(n, App(f, (u, l))))))]


def merge_equations():
    n, l, l2 = v("n"), v("l", LN), v("l2", LN)
    g = Op("merge", MERGE)
    return [Equation((), TRUE, App(g, (NIL, l2)), l2),
            Equation((), TRUE, App(g, (cons(n, l), l2)), App(g, (l, App(Op("ins", INS), (n, l2)))))]


def lps(assign, eqs=None):
    P = v("P", COL)
    k, u = v("k"), v("u")
    s = Summand("c_a", (NODE, NODE), [], TRUE, [k, u], [("P", assign(P, k, u))], "", name="c_a")
    return Lps(structs={"Node": Struct("Node", [("node", [("ix", NAT)], "")])},
               maps=[MapDecl("ins", INS), MapDecl("merge", MERGE)],
               equations=eqs if eqs is not None else ins_equations() + merge_equations(),
               params=[("P", COL)], summands=[s], init=[Bind("Lambda", (v("x"),), NIL)])


def update(P, k, val):
    return app("@func_update", P, k, val)


INSERT = lambda P, k, u: update(P, k, App(Op("ins", INS), (u, App(P, (k,)))))  # noqa: E731


class Recognised(unittest.TestCase):
    def test_the_ordered_insert_and_merge_are_recognised(self):
        s = Sorted([lps(INSERT)], {}, {}, ["Node"])
        self.assertEqual((s.inserts, s.merges, s.columns), ({"ins"}, {"merge"}, {"P"}))

    def test_another_order_is_not_an_ordered_insert(self):
        s = Sorted([lps(INSERT, ins_equations(less=">") + merge_equations())], {}, {}, ["Node"])
        self.assertEqual((s.inserts, s.columns), (set(), set()))

    def test_a_merge_into_its_own_entry_keeps_a_column_sorted(self):
        merge = lambda P, k, u: update(P, k, App(Op("merge", MERGE), (App(P, (u,)), App(P, (k,)))))  # noqa: E731
        self.assertEqual(Sorted([lps(merge)], {}, {}, ["Node"]).columns, {"P"})


class Refused(unittest.TestCase):
    def test_a_column_written_otherwise_is_not_sorted(self):
        cases = {
            "an entry set to a list": lambda P, k, u: update(P, k, cons(u, cons(k, NIL))),
            "an insert into another entry": lambda P, k, u: update(P, k, App(Op("ins", INS), (u, App(P, (u,))))),
            "two updates on one path": lambda P, k, u: update(INSERT(P, k, u), u, App(Op("ins", INS), (k, App(P, (u,))))),
        }
        for what, assign in cases.items():
            with self.subTest(what):
                self.assertEqual(Sorted([lps(assign)], {}, {}, ["Node"]).columns, set())

    def test_a_column_that_does_not_start_empty_is_not_sorted(self):
        x = lps(INSERT)
        x.init = [Bind("Lambda", (v("x"),), cons(v("x"), NIL))]
        self.assertEqual(Sorted([x], {}, {}, ["Node"]).columns, set())


class InPlace(unittest.TestCase):
    """The Rust runtime's ins_sorted: a binary search's place, which on a list sorted by
    id is where the model's walk puts u (and nothing when u is in it)."""

    @staticmethod
    def walk(u, l):
        for i, n in enumerate(l):
            if u == n:
                return l
            if u < n:
                return l[:i] + [u] + l[i:]
        return l + [u]

    @staticmethod
    def binary(u, l):
        import bisect
        i = bisect.bisect_left(l, u)
        return l if i < len(l) and l[i] == u else l[:i] + [u] + l[i:]

    def test_the_binary_search_gives_the_walk_on_sorted_lists(self):
        r = random.Random(20261023)
        for _ in range(2000):
            l = sorted(r.sample(range(60), r.randint(0, 20)))
            u = r.randrange(60)
            self.assertEqual(self.binary(u, l), self.walk(u, l))


if __name__ == "__main__":
    unittest.main()
