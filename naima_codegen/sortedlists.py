"""Sorted per-id lists (docs/spec.md §3.10.6): the columns of lists of ids that every
summand keeps in increasing id order, and the maps that insert into them.

A model may keep a list of ids as a set ordered by id: it starts empty and grows only
through an *ordered insert*, a map whose equations are exactly

    f(u, []) = [u];
    f(u, n |> l) = if(u == n, n |> l, if(p(u) < p(n), u |> (n |> l), n |> f(u, l)));

with `p` the one field of the id sort, or through an *ordered merge*, which inserts
every element of one list into another with it:

    g([], l2) = l2;
    g(n |> l, l2) = g(l, f(n, l2));

On a list sorted by id, `f(u, l)` is the list with `u` at the place a binary search
finds, and nothing else changes; the generator then emits that, in place, instead of
the walk that copies the list (O(log k) instead of O(k) for a list of k ids).

A column `P: I -> List(I)` is *sorted* when its initial value is the constant `[]` and
every summand's new value of P is, on every path of its `if`s, P itself or P with
exactly one entry updated to `f(u, Q(k))` or `g(l, Q(k))` (Q a sorted column, k the
updated key), or a call of a *sorted walk* on such an expression. A sorted walk is a
walk (§3.4.8) whose function-sorted result is derived from a parameter of that sort,
whose every step leaves that parameter so, and that is called nowhere else. By
induction on the run every value of a sorted column is then sorted by id: the empty
list is, and an ordered insert or merge into a sorted list gives a sorted list. The
analysis is the greatest fixpoint of these conditions, so a column that one summand
writes otherwise is never treated as sorted.
"""

from __future__ import annotations

from .expr import App, Bind, Op, Var, show, walk


def _head(e):
    return e.head.name if isinstance(e, App) and isinstance(e.head, Op) else None


def _is_var(e, name: str) -> bool:
    return isinstance(e, Var) and e.name == name


def _true(c) -> bool:
    """An equation without a condition (the reader gives `true`)."""
    return c is None or (isinstance(c, Op) and c.name == "true")


def _is_nil(e) -> bool:
    return isinstance(e, Op) and e.name == "[]"


def _equations(lps) -> dict:
    eqs: dict = {}
    for e in lps.equations:
        h = e.lhs.head if isinstance(e.lhs, App) else e.lhs
        eqs.setdefault(h.name, []).append(e)
    return eqs


def _id_field(lps, sort, id_sorts) -> str | None:
    """The projection of the one field of an id sort (§3.3.3), else None."""
    if sort[0] != "id" or sort[1] not in id_sorts or sort[1] not in lps.structs:
        return None
    cons = lps.structs[sort[1]].constructors
    if len(cons) != 1 or len(cons[0][1]) != 1:
        return None
    return cons[0][1][0][0] or None


def ordered_inserts(lps, id_sorts) -> set:
    """The maps whose equations are exactly an ordered insert by id (see the module)."""
    out = set()
    for m in lps.maps:
        s = m.sort
        if s[0] != "->" or len(s[1]) != 2 or s[1][1] != ("List", s[1][0]) or s[2] != s[1][1]:
            continue
        p = _id_field(lps, s[1][0], id_sorts)
        eqs = _equations(lps).get(m.name, [])
        if p is None or len(eqs) != 2:
            continue
        base = [e for e in eqs if isinstance(e.lhs, App) and _is_nil(e.lhs.args[1])]
        step = [e for e in eqs if isinstance(e.lhs, App) and _head(e.lhs.args[1]) == "|>"]
        if len(base) != 1 or len(step) != 1:
            continue
        b, st = base[0], step[0]
        if not (isinstance(b.lhs.args[0], Var) and isinstance(st.lhs.args[0], Var)):
            continue
        ub, us = b.lhs.args[0].name, st.lhs.args[0].name
        cell = st.lhs.args[1].args
        if not (isinstance(cell[0], Var) and isinstance(cell[1], Var)):
            continue
        n, l = cell[0].name, cell[1].name
        if len({us, n, l}) != 3 or not (_true(b.cond) and _true(st.cond)):
            continue
        want_b = "(%s |> [])" % ub
        want_s = "if((%s == %s), (%s |> %s), if((%s(%s) < %s(%s)), (%s |> (%s |> %s)), (%s |> %s(%s, %s))))" % (
            us, n, n, l, p, us, p, n, us, n, l, n, m.name, us, l)
        if show(b.rhs) == want_b and show(st.rhs) == want_s:
            out.add(m.name)
    return out


def ordered_merges(lps, inserts: set) -> set:
    """The maps whose equations are exactly an ordered merge through an ordered insert."""
    out = set()
    eqs_by = _equations(lps)
    for m in lps.maps:
        s = m.sort
        if s[0] != "->" or len(s[1]) != 2 or s[1][0] != s[1][1] or s[2] != s[1][0] or s[1][0][0] != "List":
            continue
        eqs = eqs_by.get(m.name, [])
        if len(eqs) != 2:
            continue
        base = [e for e in eqs if isinstance(e.lhs, App) and _is_nil(e.lhs.args[0])]
        step = [e for e in eqs if isinstance(e.lhs, App) and _head(e.lhs.args[0]) == "|>"]
        if len(base) != 1 or len(step) != 1 or not (_true(base[0].cond) and _true(step[0].cond)):
            continue
        b, st = base[0], step[0]
        if not (isinstance(b.lhs.args[1], Var) and _is_var(b.rhs, b.lhs.args[1].name)):
            continue
        cell = st.lhs.args[0].args
        l2 = st.lhs.args[1]
        if not (isinstance(cell[0], Var) and isinstance(cell[1], Var) and isinstance(l2, Var)):
            continue
        for f in inserts:
            if show(st.rhs) == "%s(%s, %s(%s, %s))" % (m.name, cell[1].name, f, cell[0].name, l2.name):
                out.add(m.name)
    return out


class Sorted:
    """The sorted columns and walks of a model (see the module), over the summands of
    every LPS whose code shares the state's representation (the acceptor's and the
    stepper's)."""

    def __init__(self, lpss: list, walks: dict, fun_maps: dict, id_sorts):
        lps = lpss[0]
        self.inserts = ordered_inserts(lps, id_sorts)
        self.merges = ordered_merges(lps, self.inserts) if self.inserts else set()
        self.columns: set = set()
        self.walks: dict = {}  # walk name -> the position of its sorted function parameter
        if not self.inserts:
            return
        init = {}
        for x in lpss:
            for (n, srt), v in zip(x.params, x.init):
                init.setdefault(n, []).append((srt, v))
        cols = set()
        for n, vals in init.items():
            srt = vals[0][0]
            if srt[0] == "->" and len(srt[1]) == 1 and srt[2] == ("List", srt[1][0]) \
                    and _id_field(lps, srt[1][0], id_sorts) is not None \
                    and all(_const_nil(v) for _, v in vals):
                cols.add(n)
        ws = {}
        for name, w in walks.items():
            fpos, k = fun_maps.get(name, ((), None))
            if k is None or k != w.ret_pos:
                continue
            m = next((m for m in lps.maps if m.name == name), None)
            if m is None:
                continue
            srt = m.sort[1][k]
            if srt[0] == "->" and len(srt[1]) == 1 and srt[2] == ("List", srt[1][0]):
                ws[name] = k
        self._lpss, self._walks = lpss, walks
        changed = True
        while changed:
            changed = False
            for n in sorted(cols):
                ok = all(self.preserves(e, n, cols, ws)
                         for x in lpss for s in x.summands for m, e in s.assigns if m == n)
                if not ok:
                    cols.discard(n)
                    changed = True
            for name in sorted(ws):
                if not self._walk_ok(name, ws[name], cols, ws):
                    del ws[name]
                    changed = True
        self.columns, self.walks = cols, ws

    # ------------------------------------------------------------------ the forms

    def preserves(self, e, p: str, cols: set, ws: dict) -> bool:
        """Whether e, a new value of the function p (a column or a walk's parameter), is
        p with at most one ordered insert or merge on every path (see the module)."""
        return self.top_calls(e, p, cols, ws) is not None

    def top_calls(self, e, p, cols, ws):
        """The sorted-walk calls on the paths of e (a list), or None when e is not of the
        form. Each path ends in p itself, one update of p's entry, or one such call."""
        out, stack = [], [e]
        while stack:
            x = stack.pop()
            if _is_var(x, p):
                continue
            h = _head(x)
            if h == "if" and len(x.args) == 3:
                stack += [x.args[1], x.args[2]]
            elif h == "@func_update" and len(x.args) == 3 and _is_var(x.args[0], p) \
                    and self.update_value(x.args[2], x.args[1], p) is not None:
                continue
            elif h in ws and _is_var(x.args[ws[h]], p) \
                    and not any(_mentions_var(a, p) for j, a in enumerate(x.args) if j != ws[h]):
                out.append(x)
            else:
                return None
        return out

    def update_value(self, v, k, p: str):
        """For the value v written at key k of p: ("ins", u) for f(u, p(k)), ("merge", l)
        for g(l, p(k)); None otherwise. The list updated is p's own entry at k."""
        h = _head(v)
        if h in self.inserts and self._own(v.args[1], k, p) and not _mentions_var(v.args[0], p):
            return ("ins", v.args[0])
        # a merge's first operand is only read: inserting its elements in any order into a
        # sorted list gives a sorted list
        if h in self.merges and self._own(v.args[1], k, p):
            return ("merge", v.args[0])
        return None

    @staticmethod
    def _own(x, k, p: str) -> bool:
        return isinstance(x, App) and _is_var(x.head, p) and len(x.args) == 1 and show(x.args[0]) == show(k)

    def _walk_ok(self, name: str, k: int, cols: set, ws: dict) -> bool:
        w = self._walks[name]
        p = w.params[k]
        if not self.preserves(w.base, p, cols, {}):
            return False
        # every step: the recursive call's argument at k leaves p of the form
        calls = [x for x in walk(w.body) if _head(x) == name]
        if not calls:
            return False
        for c in calls:
            if not self.preserves(c.args[k], p, cols, {}) or any(_mentions_var(a, p) for j, a in enumerate(c.args) if j != k):
                return False
        # called nowhere but in the new values of sorted columns
        for x in self._lpss:
            for s in x.summands:
                tops = set()
                for m, e2 in s.assigns:
                    if m in cols:
                        tops.update(id(c) for c in self.top_calls(e2, m, cols, ws) or ())
                for e in [s.cond, *s.args, *(e for _, e in s.assigns)]:
                    if any(_head(y) == name and id(y) not in tops for y in walk(e)):
                        return False
            for eq in x.equations:
                h = eq.lhs.head if isinstance(eq.lhs, App) else eq.lhs
                if h.name != name and any(_head(y) == name for y in walk(eq.rhs)):
                    return False
        return True


def _const_nil(v) -> bool:
    return isinstance(v, Bind) and v.kind == "Lambda" and len(v.vars) == 1 and _is_nil(v.body)


def _mentions_var(e, name: str) -> bool:
    return any(isinstance(x, Var) and x.name == name for x in walk(e))
