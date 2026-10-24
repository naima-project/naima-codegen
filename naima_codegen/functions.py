"""Walks and function-sorted arguments (docs/spec.md §3.4.2, §3.4.8).

A *walk* is a user map with one `List(X)` argument and two equations, one for
`[]` and one for `x |> l`, whose second right side is a walk body: a call of
itself on `l` (continue), an expression free of it (exit), an `if` of walk
bodies, or `e ⊕ R` / `R ⊕ e` with one operator `⊕` per walk. The backends
compile it into one loop; here it is recognised and described (`Walk`).

Function-sorted values may be read (`f(x)`), passed whole to a user map, or
updated in place: the result of a map that returns a function is assigned to
the parameter passed in the position it is built from (`ret_pos`), and an
accumulator a walk updates without returning it is an overlay. `check_uses`
enforces that, everything else of a function sort being G3.

Every traversal is a loop over an explicit stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .expr import App, Op, Var, Whr, walk

ACCUMULATE = ("||", "&&", "+", "|>")  # e ⊕ R
ACCUMULATE_RIGHT = ("||", "&&", "+")  # R ⊕ e


@dataclass
class Walk:
    """A walk (§3.4.8): f(a1, ..., [] | x |> l, ..., ak) with the list at `pos`."""

    name: str
    pos: int
    params: list  # the argument variables' names; the list position holds the tail l
    head: str  # x
    tail: str  # l
    base: object  # right side for []
    body: object  # right side for x |> l
    op: str | None  # the one accumulating operator, if any
    ret_pos: int | None = None  # function-sorted result: the argument it is derived from
    overlays: list = field(default_factory=list)  # function-sorted accumulators updated, not returned
    exits: list = field(default_factory=list)  # the exit values, the right side for [] first
    continues: list = field(default_factory=list)  # the calls of itself


def is_fun(lps, sort) -> bool:
    seen = 0
    while sort[0] == "id" and sort[1] in lps.aliases and seen < 100:
        sort = lps.aliases[sort[1]]
        seen += 1
    return sort[0] == "->"


def mentions(e, name: str) -> bool:
    return any(isinstance(x, Op) and x.name == name for x in walk(e))


def _head(e):
    return e.head.name if isinstance(e, App) and isinstance(e.head, Op) else None


def fe_root(e, fun_maps: dict):
    """The function-sorted variable a function expression is built from, or None.
    A function expression is `p`, `F[i -> v]`, `if(c, F1, F2)` over one root, or a
    call `g(..., F, ...)` of a map returning a function with F at its result position."""
    roots, stack = set(), [e]
    while stack:
        x = stack.pop()
        if isinstance(x, Var):
            roots.add(x.name)
            continue
        h = _head(x)
        if h == "@func_update" and len(x.args) == 3:
            stack.append(x.args[0])
        elif h == "if" and len(x.args) == 3:
            stack.extend(x.args[1:])
        elif h in fun_maps and fun_maps[h][1] is not None and len(x.args) > fun_maps[h][1]:
            stack.append(x.args[fun_maps[h][1]])
        else:
            return None
    return roots.pop() if len(roots) == 1 else None


def check_uses(lps, e, fun_vars: set, fun_maps: dict, root: str | None = None) -> list:
    """Problems with the function-sorted values in e (§3.4.8).

    With `root`, e is a function expression that must be built from `root` (an
    assignment `p := e` in place, a walk's accumulator, a returned function);
    otherwise e is an ordinary expression. Inside either, a function-sorted value
    is only read (`F(x)`) or passed to a user map as a function expression, which
    the backends give as an overlay over its root when it is not the root itself.
    `fun_vars`: the function-sorted variables in scope (Core's parameters, or a
    map's function-sorted arguments); `fun_maps`: map name -> (function-sorted
    argument positions, result position or None)."""
    out = []
    stack = [("fe", e, root)] if root is not None else [("ord", e, None)]
    while stack:
        mode, x, r = stack.pop()
        if mode == "fe":
            if r is None:
                r = fe_root(x, fun_maps)
                if r is None:
                    out.append("a function-sorted value not built from one function by updates, if and in-place calls")
                    continue
            if isinstance(x, Var):
                if x.name not in fun_vars:
                    out.append("the function-sorted %s is neither a parameter nor a function-sorted argument" % x.name)
                elif x.name != r:
                    out.append("%s where a function built from %s is expected" % (x.name, r))
                continue
            h = _head(x)
            if h == "@func_update" and len(x.args) == 3:
                stack.append(("fe", x.args[0], r))
                stack.extend(("ord", a, None) for a in x.args[1:])
            elif h == "if" and len(x.args) == 3:
                stack.append(("ord", x.args[0], None))
                stack.extend(("fe", a, r) for a in x.args[1:])
            elif h in fun_maps and fun_maps[h][1] is not None:
                fpos, k = fun_maps[h]
                for j, a in enumerate(x.args):
                    stack.append(("fe", a, r if j == k else None) if j in fpos else ("ord", a, None))
            else:
                out.append("a function-sorted value not built from %s by updates, if and in-place calls" % r)
            continue
        if isinstance(x, Var):
            if is_fun(lps, x.sort):
                out.append("the function-sorted %s used other than read, passed to a map or updated in place" % x.name)
            continue
        if isinstance(x, Whr):
            stack.append(("ord", x.body, None))
            stack.extend(("ord", d, None) for _, d in x.defs)
            continue
        if not isinstance(x, App):
            if hasattr(x, "body"):
                stack.append(("ord", x.body, None))
            continue
        if not isinstance(x.head, Op):  # a read F(a)
            stack.append(("fe", x.head, None))
            stack.extend(("ord", a, None) for a in x.args)
            continue
        h = x.head.name
        if h in fun_maps:
            fpos, k = fun_maps[h]
            if k is not None:
                out.append("%s returns a function, used here other than as a read or an argument" % h)
            for j, a in enumerate(x.args):
                stack.append(("fe", a, None) if j in fpos else ("ord", a, None))
            continue
        if h == "@func_update":
            out.append("a function update used other than as a read, an argument or an assignment to its function")
        stack.extend(("ord", a, None) for a in x.args)
    return out


def calls_in_place(e, fun_maps: dict) -> list:
    """The maps returning a function that a function expression calls (in place)."""
    return sorted({x.head.name for x in walk(e) if isinstance(x, App) and isinstance(x.head, Op)
                   and x.head.name in fun_maps and fun_maps[x.head.name][1] is not None})


def recognise_walk(lps, m, eqs) -> tuple[Walk | None, str | None]:
    """The walk m's equations define, or (None, why not) — (None, None) when they
    are not even of a walk's form (two equations over [] and x |> l)."""
    if len(eqs) != 2 or not all(isinstance(e.lhs, App) for e in eqs):
        return None, None
    a0, a1 = eqs[0].lhs.args, eqs[1].lhs.args
    if len(a0) != len(a1):
        return None, None
    pos = None
    for k, (p, q) in enumerate(zip(a0, a1)):
        if isinstance(p, Op) and p.name == "[]" and _head(q) == "|>":
            pos, nil_eq, cons_eq = k, eqs[0], eqs[1]
        elif isinstance(q, Op) and q.name == "[]" and _head(p) == "|>":
            pos, nil_eq, cons_eq = k, eqs[1], eqs[0]
    if pos is None:
        return None, None
    cons = cons_eq.lhs.args[pos]
    if not all(isinstance(v, Var) for v in cons.args):
        return None, "the list pattern is not x |> l over variables"
    head, tail = cons.args[0].name, cons.args[1].name
    names = []
    for k, (p, q) in enumerate(zip(nil_eq.lhs.args, cons_eq.lhs.args)):
        if k == pos:
            names.append(tail)
            continue
        if not (isinstance(p, Var) and isinstance(q, Var) and p.name == q.name):
            return None, "argument %d is not the same variable in both equations" % (k + 1)
        names.append(p.name)
    if len(set(names) | {head}) != len(names) + 1:
        return None, "the arguments are not distinct variables"
    f = m.name
    if mentions(nil_eq.rhs, f):
        return None, "the right side for [] mentions %s" % f
    w = Walk(f, pos, names, head, tail, nil_eq.rhs, cons_eq.rhs, None)
    ops, exits, continues = set(), [nil_eq.rhs], []
    stack = [cons_eq.rhs]
    while stack:
        r = stack.pop()
        if not mentions(r, f):
            exits.append(r)
            continue
        if isinstance(r, Whr):
            if any(mentions(d, f) for _, d in r.defs):
                return None, "a whr clause mentions %s" % f
            stack.append(r.body)
            continue
        h = _head(r)
        if h == f:
            if len(r.args) != len(names) or not (isinstance(r.args[pos], Var) and r.args[pos].name == tail):
                return None, "a call of %s not on the tail %s" % (f, tail)
            if any(mentions(a, f) for a in r.args):
                return None, "a nested call of %s" % f
            continues.append(r)
        elif h == "if" and len(r.args) == 3:
            if mentions(r.args[0], f):
                return None, "an if whose condition mentions %s" % f
            stack.extend(r.args[1:])
        elif h in ACCUMULATE and len(r.args) == 2 and not mentions(r.args[0], f):
            ops.add(h)
            stack.append(r.args[1])
        elif h in ACCUMULATE_RIGHT and len(r.args) == 2 and not mentions(r.args[1], f):
            ops.add(h)
            stack.append(r.args[0])
        else:
            return None, "the right side for x |> l is not a walk body"
    if len(ops) > 1:
        return None, "more than one accumulating operator (%s)" % ", ".join(sorted(ops))
    w.op = next(iter(ops), None)

    sorts = m.sort[1]
    fpos = [k for k, s in enumerate(sorts) if is_fun(lps, s)]
    if is_fun(lps, m.sort[2]):
        r = fe_root(nil_eq.rhs, {})
        if r not in names or names.index(r) not in fpos:
            return None, "the function-sorted result for [] is not built from a function-sorted argument"
        w.ret_pos = names.index(r)
    for c in continues:
        for k in fpos:
            a = c.args[k]
            if not (isinstance(a, Var) and a.name == names[k]) and k != w.ret_pos and k not in w.overlays:
                w.overlays.append(k)
    w.overlays.sort()
    w.exits = exits
    w.continues = continues
    return w, None


def check_map_body(lps, w, m, eqs, fun_maps: dict) -> list:
    """Problems with function-sorted values in m's equations (§3.4.8): in a walk,
    the accumulators of a continue are built from their own argument and a
    returned function from the returned argument; elsewhere, check_uses."""
    out = []
    fvars = set()
    for e in eqs:
        fvars |= {v.name for v in e.vars if is_fun(lps, v.sort)}
    ret_var = None
    if w is not None:
        if w.ret_pos is not None:
            ret_var = w.params[w.ret_pos]
        for x in w.exits:
            out += check_uses(lps, x, fvars, fun_maps, ret_var)
        stack = [w.body]
        while stack:
            r = stack.pop()
            if not mentions(r, m.name):
                continue  # an exit, checked above
            if isinstance(r, Whr):
                stack.append(r.body)
                for _, d in r.defs:
                    out += check_uses(lps, d, fvars, fun_maps)
                continue
            h = _head(r)
            if h == m.name:
                for k, a in enumerate(r.args):
                    root = w.params[k] if is_fun(lps, m.sort[1][k]) else None
                    out += check_uses(lps, a, fvars, fun_maps, root)
            elif h == "if":
                out += check_uses(lps, r.args[0], fvars, fun_maps)
                stack.extend(r.args[1:])
            else:  # the accumulating operator
                for a in r.args:
                    if mentions(a, m.name):
                        stack.append(a)
                    else:
                        out += check_uses(lps, a, fvars, fun_maps)
        return out
    for e in eqs:
        if m.sort[0] == "->" and is_fun(lps, m.sort[2]):
            k = ret_position(lps, m, eqs)
            root = e.lhs.args[k].name if k is not None else None
            if root is None:
                continue  # reported by the front end (§3.4.8)
            out += check_uses(lps, e.rhs, fvars, fun_maps, root)
        else:
            out += check_uses(lps, e.rhs, fvars, fun_maps)
    return out


def ret_position(lps, m, eqs) -> int | None:
    """The argument position a non-walk map's function-sorted result is built from."""
    if len(eqs) != 1 or not isinstance(eqs[0].lhs, App):
        return None
    r = fe_root(eqs[0].rhs, {})
    for k, a in enumerate(eqs[0].lhs.args):
        if isinstance(a, Var) and a.name == r and is_fun(lps, m.sort[1][k]):
            return k
    return None
