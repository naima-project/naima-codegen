"""Sorts and data expressions of the linear process, read from its ATerm
(docs/spec.md §3.2.2: every variable and operator carries its sort).

A sort is a tuple: `("id", name)`, `(kind, element)` with kind one of `List`,
`FSet`, `FBag`, `Set`, `Bag`, or `("->", domains, codomain)`.

An expression is one of `Var`, `Op`, `Num`, `App`, `Bind`, `Whr`. Every walk is
a loop over an explicit stack, since expressions are as deep as the model
makes them.
"""

from __future__ import annotations

from dataclasses import dataclass

from .aterm import Appl

# --------------------------------------------------------------------- sorts

_CONS = {"SortList": "List", "SortSet": "Set", "SortBag": "Bag", "SortFSet": "FSet", "SortFBag": "FBag"}


def sort_of(term) -> tuple:
    """The sort an ATerm sort expression denotes."""
    out: list = []
    # Post-order over the sort term: frames (term, visited).
    stack = [(term, False)]
    while stack:
        t, seen = stack.pop()
        if not isinstance(t, Appl):
            raise ValueError("not a sort: %r" % (t,))
        if t.name == "SortId":
            out.append(("id", t.args[0].name))
        elif t.name == "SortCons":
            if not seen:
                stack.append((t, True))
                stack.append((t.args[1], False))
            else:
                out.append((_CONS.get(t.args[0].name, t.args[0].name), out.pop()))
        elif t.name == "SortArrow":
            doms = t.args[0]
            if not seen:
                stack.append((t, True))
                stack.append((t.args[1], False))
                for d in reversed(doms):
                    stack.append((d, False))
            else:
                cod = out.pop()
                ds = out[len(out) - len(doms):]
                del out[len(out) - len(doms):]
                out.append(("->", tuple(ds), cod))
        elif t.name == "SortStruct":
            out.append(("struct", t))
        else:
            raise ValueError("unknown sort term %s" % t.name)
    return out[0]


def sort_str(s: tuple) -> str:
    """The sort in mCRL2 syntax."""
    out: list = []
    stack = [(s, False)]
    while stack:
        x, seen = stack.pop()
        if x[0] == "id":
            out.append(x[1])
        elif x[0] == "struct":
            out.append("struct ...")
        elif x[0] == "->":
            if not seen:
                stack.append((x, True))
                stack.append((x[2], False))
                for d in reversed(x[1]):
                    stack.append((d, False))
            else:
                cod = out.pop()
                n = len(x[1])
                ds = out[len(out) - n:]
                del out[len(out) - n:]
                out.append("%s -> %s" % (" # ".join("(%s)" % d if "->" in d else d for d in ds), cod))
        else:
            if not seen:
                stack.append((x, True))
                stack.append((x[1], False))
            else:
                out.append("%s(%s)" % (x[0], out.pop()))
    return out[0]


def sort_parts(s: tuple):
    """Every sort occurring in s, s included, outermost first."""
    stack = [s]
    while stack:
        x = stack.pop()
        yield x
        if x[0] == "->":
            stack.append(x[2])
            stack.extend(reversed(x[1]))
        elif x[0] in ("List", "FSet", "FBag", "Set", "Bag"):
            stack.append(x[1])


BOOL = ("id", "Bool")
NAT = ("id", "Nat")
POS = ("id", "Pos")
INT = ("id", "Int")
REAL = ("id", "Real")

# --------------------------------------------------------------- expressions


@dataclass(frozen=True, slots=True, eq=False)
class Var:
    name: str
    sort: tuple


@dataclass(frozen=True, slots=True, eq=False)
class Op:
    name: str
    sort: tuple


@dataclass(frozen=True, slots=True, eq=False)
class Num:
    value: int
    sort: tuple


@dataclass(frozen=True, slots=True, eq=False)
class App:
    head: object
    args: tuple


@dataclass(frozen=True, slots=True, eq=False)
class Bind:
    kind: str  # Forall, Exists, Lambda, SetComp, BagComp
    vars: tuple  # of Var
    body: object


@dataclass(frozen=True, slots=True, eq=False)
class Whr:
    body: object
    defs: tuple  # of (Var, expr)


def children(e) -> tuple:
    """The direct subexpressions of e, in order."""
    if isinstance(e, App):
        return (e.head,) + e.args
    if isinstance(e, Bind):
        return e.vars + (e.body,)
    if isinstance(e, Whr):
        out = [e.body]
        for v, x in e.defs:
            out.append(v)
            out.append(x)
        return tuple(out)
    return ()


def walk(e):
    """Every subexpression of e, e first (pre-order)."""
    stack = [e]
    while stack:
        x = stack.pop()
        yield x
        stack.extend(reversed(children(x)))


def rebuild(e, fn):
    """e with fn applied bottom-up: fn receives each node with rebuilt children
    and returns its replacement."""
    out: list = []
    stack = [(e, False)]
    while stack:
        x, seen = stack.pop()
        kids = children(x)
        if not kids:
            out.append(fn(x))
            continue
        if not seen:
            stack.append((x, True))
            for k in reversed(kids):
                stack.append((k, False))
            continue
        n = len(kids)
        new = out[len(out) - n:]
        del out[len(out) - n:]
        if isinstance(x, App):
            y = App(new[0], tuple(new[1:]))
        elif isinstance(x, Bind):
            y = Bind(x.kind, tuple(new[: len(x.vars)]), new[len(x.vars)])
        else:
            defs = tuple((new[1 + 2 * i], new[2 + 2 * i]) for i in range(len(x.defs)))
            y = Whr(new[0], defs)
        out.append(fn(y))
    return out[0]


def same(a, b) -> bool:
    """a and b are the same term (structurally; the classes compare by identity)."""
    stack = [(a, b)]
    while stack:
        x, y = stack.pop()
        if type(x) is not type(y):
            return False
        if isinstance(x, (Var, Op)):
            if x.name != y.name or x.sort != y.sort:
                return False
        elif isinstance(x, Num):
            if x.value != y.value or x.sort != y.sort:
                return False
        elif isinstance(x, Bind):
            if x.kind != y.kind or len(x.vars) != len(y.vars):
                return False
            stack.extend(zip(x.vars + (x.body,), y.vars + (y.body,)))
        elif isinstance(x, (App, Whr)):
            kx, ky = children(x), children(y)
            if len(kx) != len(ky):
                return False
            stack.extend(zip(kx, ky))
        elif x != y:
            return False
    return True


def drop_reflexive(e):
    """§3.2.3: e with every `t == t` replaced by true and `true` dropped from
    conjunctions. Sum elimination leaves such conjuncts (a variable fixed by
    `d == head(NQ)` becomes `head(NQ) == head(NQ)`); the rewriter used to remove
    them, and without rewriting they would be evaluated, before the conjuncts
    that make them defined (`NQ != []`)."""
    def fn(x):
        if isinstance(x, App) and isinstance(x.head, Op) and len(x.args) == 2:
            if x.head.name == "==" and same(x.args[0], x.args[1]):
                return Op("true", BOOL)
            if x.head.name == "&&":
                l, r = x.args
                if isinstance(l, Op) and l.name == "true":
                    return r
                if isinstance(r, Op) and r.name == "true":
                    return l
        return x
    return rebuild(e, fn)


def free_vars(e) -> set:
    """The names of the variables free in e."""
    free: set = set()
    # (expr, bound names) frames; bound sets are shared, copied on extension.
    stack = [(e, frozenset())]
    while stack:
        x, bound = stack.pop()
        if isinstance(x, Var):
            if x.name not in bound:
                free.add(x.name)
        elif isinstance(x, App):
            stack.append((x.head, bound))
            for a in x.args:
                stack.append((a, bound))
        elif isinstance(x, Bind):
            stack.append((x.body, bound | {v.name for v in x.vars}))
        elif isinstance(x, Whr):
            stack.append((x.body, bound | {v.name for v, _ in x.defs}))
            for _, d in x.defs:
                stack.append((d, bound))
    return free


def rename_vars(e, mapping: dict):
    """e with variables renamed by mapping (names absent from it are kept)."""

    def fn(x):
        if isinstance(x, Var) and x.name in mapping:
            return Var(mapping[x.name], x.sort)
        return x

    return rebuild(e, fn)


# ------------------------------------------------------------ from the ATerm

_NUM_HEADS = {"@cReal", "@most_significant_digitNat", "@most_significant_digit", "@cNat", "@cInt", "@cNeg", "@concat_digit", "@cDub"}


def expr_of(term):
    """The expression an ATerm data expression denotes; numerals become Num."""
    out: list = []
    stack = [(term, False)]
    while stack:
        t, seen = stack.pop()
        if isinstance(t, int):
            out.append(t)
            continue
        if not isinstance(t, Appl):
            raise ValueError("not a data expression: %r" % (t,))
        name = t.name
        if name == "DataVarId":
            out.append(Var(t.args[0].name, sort_of(t.args[1])))
        elif name == "OpId":
            s = sort_of(t.args[1])
            if t.args[0].name == "@c0":
                out.append(Num(0, NAT))
            elif t.args[0].name == "@c1":
                out.append(Num(1, POS))
            else:
                out.append(Op(t.args[0].name, s))
        elif name == "DataAppl":
            if not seen:
                stack.append((t, True))
                for a in reversed(t.args):
                    stack.append((a, False))
            else:
                n = len(t.args)
                parts = out[len(out) - n:]
                del out[len(out) - n:]
                out.append(_fold_number(parts[0], tuple(parts[1:])))
        elif name == "Binder":
            if not seen:
                stack.append((t, True))
                stack.append((t.args[2], False))
            else:
                vs = tuple(Var(v.args[0].name, sort_of(v.args[1])) for v in t.args[1])
                out.append(Bind(t.args[0].name, vs, out.pop()))
        elif name == "Whr":
            defs = t.args[1]
            if not seen:
                stack.append((t, True))
                for d in reversed(defs):
                    stack.append((d.args[1], False))
                stack.append((t.args[0], False))
            else:
                n = len(defs)
                vals = out[len(out) - n:]
                del out[len(out) - n:]
                body = out.pop()
                out.append(Whr(body, tuple((Var(d.args[0].args[0].name, sort_of(d.args[0].args[1])), v) for d, v in zip(defs, vals))))
        else:
            raise ValueError("unknown data expression %s" % name)
    return out[0]


def _fold_number(head, args):
    if isinstance(head, Op) and head.name in _NUM_HEADS:
        vals = [a.value if isinstance(a, Num) else a for a in args]
        if all(isinstance(v, int) for v in vals):
            n = head.name
            if n == "@most_significant_digitNat":
                return Num(vals[0], NAT)
            if n == "@most_significant_digit":
                return Num(vals[0], POS)
            if n == "@cNat":
                return Num(vals[0], NAT)
            if n == "@cInt":
                return Num(vals[0], INT)
            if n == "@cNeg":
                return Num(-vals[0], INT)
            if n == "@cDub" and len(vals) == 2:
                return Num(2 * vals[1] + (1 if isinstance(args[0], Op) and args[0].name == "true" else 0), POS)
            if n == "@cReal" and len(vals) == 2 and vals[1] == 1:
                return Num(vals[0], REAL)
            if n == "@concat_digit":
                return Num(vals[0] * (1 << 64) + vals[1], args[0].sort if isinstance(args[0], Num) else NAT)
    return App(head, args)


# --------------------------------------------------------------- printing

_INFIX = {
    "&&": "&&", "||": "||", "=>": "=>", "==": "==", "!=": "!=", "<": "<", "<=": "<=", ">": ">", ">=": ">=",
    "+": "+", "-": "-", "*": "*", "div": "div", "mod": "mod", "in": "in", "|>": "|>", "<|": "<|", "++": "++",
    ".": ".",
}
_NAMES = {"@fset_empty": "{}", "@func_update": None, "@fset_cons": None, "@fset_insert": None}


def show(e) -> str:
    """e in mCRL2-like syntax, binary operators fully parenthesised."""
    out: list = []
    stack = [(e, False)]
    while stack:
        x, seen = stack.pop()
        if isinstance(x, Var):
            out.append(x.name)
        elif isinstance(x, Num):
            out.append(str(x.value))
        elif isinstance(x, Op):
            out.append("[]" if x.name == "[]" else x.name)
        elif isinstance(x, App):
            if not seen:
                stack.append((x, True))
                for k in reversed((x.head,) + x.args):
                    stack.append((k, False))
                continue
            n = len(x.args) + 1
            parts = out[len(out) - n:]
            del out[len(out) - n:]
            h, a = parts[0], parts[1:]
            hn = x.head.name if isinstance(x.head, Op) else None
            if hn in _INFIX and len(a) == 2:
                out.append("(%s %s %s)" % (a[0], _INFIX[hn], a[1]))
            elif hn == "!" and len(a) == 1:
                out.append("!" + a[0])
            elif hn == "-" and len(a) == 1:
                out.append("-" + a[0])
            elif hn == "#" and len(a) == 1:
                out.append("#" + a[0])
            elif hn == "@func_update" and len(a) == 3:
                out.append("%s[%s -> %s]" % (a[0], a[1], a[2]))
            elif hn in ("@fset_cons", "@fset_insert") and len(a) == 2:
                out.append("({%s} + %s)" % (a[0], a[1]))
            else:
                out.append("%s(%s)" % (h, ", ".join(a)))
        elif isinstance(x, Bind):
            if not seen:
                stack.append((x, True))
                stack.append((x.body, False))
                continue
            body = out.pop()
            vs = ", ".join("%s: %s" % (v.name, sort_str(v.sort)) for v in x.vars)
            out.append("(%s %s . %s)" % (x.kind.lower(), vs, body))
        elif isinstance(x, Whr):
            if not seen:
                stack.append((x, True))
                for _, d in reversed(x.defs):
                    stack.append((d, False))
                stack.append((x.body, False))
                continue
            n = len(x.defs)
            ds = out[len(out) - n:]
            del out[len(out) - n:]
            body = out.pop()
            out.append("(%s whr %s end)" % (body, ", ".join("%s = %s" % (v.name, d) for (v, _), d in zip(x.defs, ds))))
        else:
            out.append(repr(x))
    return out[0]
