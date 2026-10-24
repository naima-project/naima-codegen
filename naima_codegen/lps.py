"""The linear process as the generator reads it (docs/spec.md §3.2, §3.5).

`read_lps` turns the ATerm of `lpspp --format=internal` into an `Lps`:
the data specification (sorts, maps, equations), the action declarations and
the process (parameters, summands, initial state), with

- names recovered (§3.5.3): the `_Core[0-9]*` suffix that linearisation adds to
  parameters and sum variables is removed, and sum variables whose recovered
  names coincide within a summand are numbered in order of appearance (the
  second `n` becomes `n_2`); `mcrl22lps -n` (§3.2.2) never clusters, so
  one LPS summand is one source summand and nothing is split here;
- summands in the order the LPS lists them, which with the pinned command is
  the source order of `proc Core` (docs/spec.md §3.5.4; the front end
  checks it against the source), named `label` or `label#k` by their ordinal
  among the summands of their label.

Nothing here judges the model: the checks are in `frontend`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import aterm
from .aterm import Appl
from .expr import expr_of, sort_of

_SUFFIX = re.compile(r"_Core[0-9]*$")
UNDEFINED_TIME = "@undefined_real"


def recover(name: str) -> str:
    """A parameter's or sum variable's source name (§3.5.3)."""
    return _SUFFIX.sub("", name)


@dataclass
class Struct:
    """A structured sort: constructors, each with (projection, sort) fields and a recogniser."""

    name: str
    constructors: list  # of (name, [(projection or "", sort)], recogniser or "")

    @property
    def shape(self) -> str:
        if all(not f for _, f, _ in self.constructors):
            return "enumeration"
        if len(self.constructors) == 1:
            return "record"
        return "union"


@dataclass
class MapDecl:
    name: str
    sort: tuple

    @property
    def arity(self) -> int:
        return len(self.sort[1]) if self.sort[0] == "->" else 0


@dataclass
class Equation:
    vars: tuple
    cond: object
    lhs: object
    rhs: object


@dataclass
class ActDecl:
    name: str
    sorts: tuple


@dataclass
class Summand:
    """One alternative of Core: `sum vars . cond -> label(args) . Core(assigns)`."""

    label: str
    sorts: tuple  # the action's argument sorts (an overloaded label is told apart by them)
    sumvars: list  # of (name, sort)
    cond: object
    args: list
    assigns: list  # of (parameter name, expr), parameters not assigned keep their value
    cond_text: str
    n_actions: int = 1
    timed: bool = False
    name: str = ""


@dataclass
class Lps:
    structs: dict = field(default_factory=dict)  # name -> Struct
    aliases: dict = field(default_factory=dict)  # name -> sort
    maps: list = field(default_factory=list)  # MapDecl, user and catalogue
    equations: list = field(default_factory=list)  # Equation
    acts: list = field(default_factory=list)  # ActDecl
    globals: list = field(default_factory=list)  # Var
    params: list = field(default_factory=list)  # (recovered name, sort)
    summands: list = field(default_factory=list)  # Summand, canonical order, delta left out
    deltas: int = 0  # `true -> delta` and other delta summands
    init: list = field(default_factory=list)  # expr per parameter


def _sort_name(t) -> str:
    return t.args[0].name


def read_lps(text: str) -> Lps:
    """The Lps of the ATerm text that `lpspp --format=internal` printed."""
    spec = aterm.parse(text)
    if not isinstance(spec, Appl) or spec.name != "LinProcSpec":
        raise ValueError("not a linear process specification (LinProcSpec)")
    data, actspec, globspec, proc, init = spec.args
    lps = Lps()
    sortspec, consspec, mapspec, eqnspec = data.args

    # Sorts.
    for ref in sortspec.args[0]:
        name = _sort_name(ref.args[0])
        body = ref.args[1]
        if body.name == "SortStruct":
            cons = []
            for c in body.args[0]:
                fields = [(p.args[0].name, sort_of(p.args[1])) for p in c.args[1]]
                cons.append((c.args[0].name, fields, c.args[2].name if len(c.args) > 2 else ""))
            lps.structs[name] = Struct(name, cons)
        else:
            lps.aliases[name] = sort_of(body)

    for op in list(consspec.args[0]) + list(mapspec.args[0]):
        lps.maps.append(MapDecl(op.args[0].name, sort_of(op.args[1])))
    for eq in eqnspec.args[0]:
        vs, cond, lhs, rhs = eq.args
        lps.equations.append(Equation(tuple(expr_of(v) for v in vs), expr_of(cond), expr_of(lhs), expr_of(rhs)))

    for a in actspec.args[0]:
        lps.acts.append(ActDecl(a.args[0].name, tuple(sort_of(s) for s in a.args[1])))
    lps.globals = [expr_of(v) for v in globspec.args[0]]

    # Parameters.
    raw_params = proc.args[0]
    rename = {}
    for p in raw_params:
        rn = recover(p.args[0].name)
        rename[p.args[0].name] = rn
        lps.params.append((rn, sort_of(p.args[1])))
    pnames = {n for n, _ in lps.params}

    # Summands.
    found = []
    for s in proc.args[1]:
        sumvars, cond, act, time, assigns = s.args[0], s.args[1], s.args[2], s.args[3], s.args[4]
        if isinstance(act, Appl) and act.name == "Delta":
            lps.deltas += 1
            continue
        found.append(_summand((tuple(sumvars), cond, act, tuple(assigns)), time, rename, pnames))

    seen: dict = {}
    for s in found:
        k = seen[s.label] = seen.get(s.label, 0) + 1
        s.name = s.label if k == 1 else "%s#%d" % (s.label, k)
    lps.summands = found

    lps.init = [expr_of(v) for v in init.args[0]]
    return lps


def _summand(alt, time, rename, pnames) -> Summand:
    sumvars, cond, act, assigns = alt
    local = dict(rename)
    taken = set(pnames)
    svs = []
    for v in sumvars:
        base = recover(v.args[0].name)
        nm, k = base, 1
        while nm in taken:
            k += 1
            nm = "%s_%d" % (base, k)
        taken.add(nm)
        local[v.args[0].name] = nm
        svs.append((nm, sort_of(v.args[1])))

    def ren(t):
        if t.name == "DataVarId" and t.args[0].name in local:
            return Appl("DataVarId", (Appl(local[t.args[0].name], ()), t.args[1]))
        return t

    cond = aterm.rebuild(cond, ren)
    act = aterm.rebuild(act, ren)
    assigns = tuple(aterm.rebuild(a, ren) for a in assigns)
    actions = act.args[0] if act.name == "TimedMultAct" else act.args[0] if act.args else ()
    timed = not (isinstance(time, Appl) and time.name == "DataVarId" and time.args[0].name == UNDEFINED_TIME)
    if act.name == "TimedMultAct":
        t2 = act.args[1]
        timed = timed or not (t2.name == "DataVarId" and t2.args[0].name == UNDEFINED_TIME)
    if actions:
        a0 = actions[0]
        label = a0.args[0].args[0].name
        sorts = tuple(sort_of(s) for s in a0.args[0].args[1])
        args = [expr_of(x) for x in a0.args[1]]
    else:
        label, sorts, args = "tau", (), []
    return Summand(
        label=label,
        sorts=sorts,
        sumvars=svs,
        cond=expr_of(cond),
        args=args,
        assigns=[(a.args[0].args[0].name, expr_of(a.args[1])) for a in assigns],
        cond_text=aterm.to_text(cond),
        n_actions=len(actions),
        timed=timed,
    )
