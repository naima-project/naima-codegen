"""What both backends need from an accepted model, computed once
(docs/spec.md §3.5.5, §3.8, §3.10): the shape of every structured sort and
how its values are represented, how an entry binds a summand's sum variables,
and the order in which a summand's function-sorted assignments are applied in
place (§3.4.8, §3.8.4).

Every traversal is a loop over an explicit stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import Diagnostic, GeneratorError
from .expr import App, Op, Var, free_vars, walk
from .functions import is_fun

CORE = "model/core.mcrl2"


def g10(where: str, message: str) -> GeneratorError:
    """A construction assertion failed (§3.9.1): a defect of the generator or a form it does not compile."""
    return GeneratorError([Diagnostic("G10", CORE, where, "§3.9.1", message)])


@dataclass
class Cons:
    sort: str
    index: int
    name: str
    fields: list  # of (projection, sort)
    shape: str  # id, enumeration, record, union


class Types:
    """Structured sorts and their representation (§3.10.3).

    An id sort is its Nat; an enumeration the index of its constructor; a record
    the tuple of its fields; a union the tuple (constructor index, fields...)."""

    def __init__(self, lps, id_sorts):
        self.lps = lps
        self.ids = set(id_sorts)
        self.cons: dict = {}
        self.proj: dict = {}  # projection -> [(Cons, field index)]
        self.recog: dict = {}  # recogniser -> Cons
        self.shape: dict = {}
        for name, st in lps.structs.items():
            shape = "id" if name in self.ids else st.shape
            self.shape[name] = shape
            for k, (c, fields, r) in enumerate(st.constructors):
                cn = Cons(name, k, c, list(fields), shape)
                self.cons[c] = cn
                for j, (p, _) in enumerate(fields):
                    if p:
                        self.proj.setdefault(p, []).append((cn, j))
                if r:
                    self.recog[r] = cn

    def resolve(self, s: tuple) -> tuple:
        seen = 0
        while s[0] == "id" and s[1] in self.lps.aliases and seen < 100:
            s = self.lps.aliases[s[1]]
            seen += 1
        return s

    def kind(self, s: tuple) -> str:
        s = self.resolve(s)
        if s[0] == "->":
            return "fun"
        if s[0] in ("List", "FSet", "FBag"):
            return s[0]
        if s[1] in ("Bool",):
            return "bool"
        if s[1] in ("Nat", "Pos", "Int"):
            return "int"
        return self.shape.get(s[1], "unknown")


@dataclass
class Bind:
    """How an entry's arguments bind a sum variable: argument position, then steps
    into constructor fields; `checks` are the constructor tags met on the way."""

    var: str
    arg: int
    steps: list  # of (Cons, field index)


@dataclass
class SummandPlan:
    name: str
    binds: list = field(default_factory=list)  # Bind
    fixed: list = field(default_factory=list)  # (var, expr) in dependency order
    tags: list = field(default_factory=list)  # (arg, steps, Cons): the value at that path has this constructor
    fun_order: list = field(default_factory=list)  # function-sorted parameters assigned, in application order
    reads: dict = field(default_factory=dict)  # parameter -> function-sorted parameters its right side reads


def plan_summand(types: Types, s, fun_params: set, fun_maps: dict | None = None) -> SummandPlan:
    """§3.5.5, §3.8.2: binding by whole arguments and constructor fields, then the
    equality-fixed variables in an order where each one's right side is bound."""
    p = SummandPlan(s.name)
    sumvars = [n for n, _ in s.sumvars]
    bound = set()
    for j, a in enumerate(s.args):
        stack = [(a, [])]
        while stack:
            x, steps = stack.pop()
            if isinstance(x, Var) and x.name in sumvars and x.name not in bound:
                p.binds.append(Bind(x.name, j, steps))
                bound.add(x.name)
            elif isinstance(x, App) and isinstance(x.head, Op) and x.head.name in types.cons:
                c = types.cons[x.head.name]
                if c.shape == "union":
                    p.tags.append((j, steps, c))
                for k, y in enumerate(x.args):
                    stack.append((y, steps + [(c, k)]))
    conj = []
    stack = [s.cond]
    while stack:
        c = stack.pop()
        if isinstance(c, App) and isinstance(c.head, Op) and c.head.name == "&&" and len(c.args) == 2:
            stack.append(c.args[1])
            stack.append(c.args[0])
        else:
            conj.append(c)
    pending = [n for n in sumvars if n not in bound]
    while pending:
        progress = False
        for c in conj:
            if not (isinstance(c, App) and isinstance(c.head, Op) and c.head.name == "==" and len(c.args) == 2):
                continue
            for a, b in ((c.args[0], c.args[1]), (c.args[1], c.args[0])):
                if isinstance(a, Var) and a.name in pending and not ((free_vars(b) & set(sumvars)) - bound):
                    p.fixed.append((a.name, b))
                    bound.add(a.name)
                    pending.remove(a.name)
                    progress = True
                    break
        if not progress:
            raise g10("summand %s" % s.name, "sum variables %s cannot be bound (§3.5.5)" % ", ".join(pending))
    # §3.4.8, §3.8.4: function-sorted assignments in place, each after every other
    # right side that reads its parameter.
    funs = [(n, e) for n, e in s.assigns if n in fun_params]
    names = [n for n, _ in funs]
    for n, e in funs:
        if fun_maps is None:
            p.reads[n] = {x.name for x in walk(e) if isinstance(x, Var)} & set(names) - {n}
            continue
        # Only what an in-place call reads when it runs: its function-sorted arguments
        # other than the one it updates; indices, values and conditions are evaluated
        # before anything is written (§3.8.4).
        r = set()
        for x in walk(e):
            if isinstance(x, App) and isinstance(x.head, Op) and x.head.name in fun_maps:
                fpos, k = fun_maps[x.head.name]
                for j in fpos:
                    if j != k and j < len(x.args):
                        r |= {y.name for y in walk(x.args[j]) if isinstance(y, Var)}
        p.reads[n] = r & set(names) - {n}
    order, done = [], set()
    while len(order) < len(names):
        ready = [n for n in names if n not in done and all(m in done or n not in p.reads[m] for m in names if m != n and m not in done)]
        # n is ready when no assignment still to apply reads n
        if not ready:
            raise g10("summand %s" % s.name, "the in-place updates of %s read each other (§3.4.8)" % ", ".join(sorted(set(names) - done)))
        for n in ready:
            order.append(n)
            done.add(n)
    p.fun_order = order
    return p


def fun_params(lps) -> set:
    return {n for n, s in lps.params if is_fun(lps, s)}


def codec_desc(types: Types, sort) -> tuple:
    """The shape the entry codec (§3.8.10) follows for a value of `sort`:
    ("b",) Bool, ("n",) Nat/Pos/id/enumeration index, ("i",) Int, ("r", fields)
    a record, ("u", (fields per constructor)) a union, ("l", element) a list.
    The bytes: Bool one byte; Nat unsigned LEB128; Int zigzag LEB128; a record its
    fields in order; a union its constructor's index then its fields; a list its
    length then its elements; an entry its side (0 event, 1 action), its label's
    code, then its arguments."""
    out: list = []
    stack = [(sort, False)]
    while stack:
        s, seen = stack.pop()
        s = types.resolve(s)
        k = types.kind(s)
        if k == "bool":
            out.append(("b",))
        elif k == "int":
            out.append(("i",) if s[1] == "Int" else ("n",))
        elif k in ("id", "enumeration"):
            out.append(("n",))
        elif k == "List":
            if not seen:
                stack.append((s, True))
                stack.append((s[1], False))
            else:
                out.append(("l", out.pop()))
        elif k in ("record", "union"):
            cons = types.lps.structs[s[1]].constructors
            fields = [fs for _, f, _ in cons for _, fs in f]
            if not seen:
                stack.append((s, True))
                for fs in reversed(fields):
                    stack.append((fs, False))
                continue
            vals = out[len(out) - len(fields):] if fields else []
            del out[len(out) - len(fields):]
            if k == "record":
                out.append(("r", tuple(vals)))
            else:
                per, i = [], 0
                for _, f, _ in cons:
                    per.append(tuple(vals[i:i + len(f)]))
                    i += len(f)
                out.append(("u", tuple(per)))
        else:
            raise g10("sort %s" % (s,), "no entry encoding for it (§3.5.7)")
    return out[0]


_RANGES = {"u8": (0, 2**8 - 1), "u16": (0, 2**16 - 1), "u32": (0, 2**32 - 1), "u64": (0, 2**64 - 1),
           "i32": (-2**31, 2**31 - 1), "i64": (-2**63, 2**63 - 1)}


def width_range(model, name: str):
    """(lo, hi) a parameter's integer values must fit (§3.6.2, §3.6.6, §3.12.4), for a
    Nat, Pos or Int parameter or a function into one; None otherwise."""
    lps = model.lps
    s = dict(lps.params)[name]
    t = Types(lps, model.config.id_sorts)
    s = t.resolve(s)
    if s[0] == "->":
        s = t.resolve(s[2])
    if s[0] != "id" or s[1] not in ("Nat", "Pos", "Int"):
        return None
    p = next((p for p in model.params if p.name == name), None)
    w = (p.width if p else None) or model.config.widths.get(s[1]) or ("i64" if s[1] == "Int" else "u64")
    lo, hi = _RANGES[w]
    return (max(lo, 1) if s[1] == "Pos" else lo), hi
