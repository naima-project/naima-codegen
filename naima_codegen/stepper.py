"""The refined Core as the backends read it (docs/spec.md §3.7.5): the LPS of
the stepper Core of `refine` (the refined Core without the priority and `Q`
conjuncts, which the output loop of §3.8.3 makes true), with

- the `ix_*` parameters and their upkeep left out: the code keeps the indexes
  instead (§3.7.5, §3.8.3, §3.10.3);
- the summands named as `refine` wrote them: the inputs in source order, then
  the outputs in policy order;
- the construction assertions of §3.9.1 (G10).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .errors import Diagnostic, GeneratorError
from .expr import App, Op, Var, walk
from .frontend import CORE, Model, _check_maps, _Report, linearisation_input
from .lps import read_lps
from .lower import Types
from .policy import POLICY
from .refine import Refined
from .source import read_core


@dataclass
class IndexCode:
    """What the backends emit for one index (§3.7.6, §3.10.3)."""

    name: str
    sort: str  # the id sort of its members
    order: str
    eqs: dict  # "mem", "cls", "adm", "key", "bound" -> (argument variables, right side)
    classes: list = field(default_factory=list)  # constructors of the class sort, declaration order
    cls_sort: str | None = None
    watched: list = field(default_factory=list)  # parameters whose writes dirty an id (member, class, key)


@dataclass
class Stepper:
    model: Model  # the refined model: lps of the stepper Core, maps of the policy included
    refined: Refined
    inputs: list  # summand indices into model.lps.summands
    outputs: list  # in policy order
    indexes: list  # IndexCode
    watched: dict  # parameter -> id sort of the ids its writes dirty


def build(model: Model, refined: Refined, core_text: str, init_text: str, tools) -> Stepper:
    src = read_core(core_text)
    k = init_text.rstrip().rfind(")")
    init2 = init_text[:k] + refined.init_suffix + init_text[k:]
    aterm = tools.linearise(linearisation_input(refined.stepper, init2, src, model.config), "generated/model/core-refined.mcrl2")
    lps = read_lps(aterm)
    probs = []

    def g10(where, msg):
        probs.append(Diagnostic("G10", "generated/model/core-refined.mcrl2", where, "§3.9.1", msg))

    names = [s.name for s in refined.inputs] + [s.name for s in refined.outputs]
    if [s.label for s in lps.summands] != [n.split("#")[0] for n in names]:
        g10("proc Core", "the LPS of the refined Core does not list its summands as written")
        raise GeneratorError(probs)
    for s, n in zip(lps.summands, names):
        s.name = n
    # The ix_ parameters: the code keeps indexes instead.
    keep = [i for i, (n, _) in enumerate(lps.params) if not n.startswith("ix_")]
    lps.params = [lps.params[i] for i in keep]
    lps.init = [lps.init[i] for i in keep]
    for s in lps.summands:
        s.assigns = [(n, e) for n, e in s.assigns if not n.startswith("ix_")]
    if [n for n, _ in lps.params] != [n for n, _ in model.lps.params]:
        g10("proc Core", "the refined Core's parameters differ from Core's")
    kinds = {}
    inputs, outputs = [], []
    for i, s in enumerate(lps.summands):
        if i < len(refined.inputs):
            kinds[s.name] = "input"
            inputs.append(i)
            for v, _ in s.sumvars:
                if not any(isinstance(x, Var) and x.name == v for a in s.args for x in walk(a)):
                    g10("summand %s" % s.name, "the sum variable %s is not an action argument" % v)
        else:
            kinds[s.name] = "output"
            outputs.append(i)
            if s.sumvars:
                g10("summand %s" % s.name, "a refined output with sum variables %s" % ", ".join(v for v, _ in s.sumvars))
    # The policy's maps (p_*) are user maps, compiled like the model's (§3.7.1).
    known = {m.name for m in model.lps.maps}
    pmaps = [m for m in lps.maps if m.name.startswith("p_") and m.name not in known]
    rep = _Report()
    pwalks, pfun = _check_maps(lps, model.config, pmaps, rep)
    for d in rep.items:
        if "p_" not in d.where:
            continue  # the model's own maps were judged by the front end
        probs.append(Diagnostic(d.code, POLICY, d.where, d.section, d.message))
    if probs:
        raise GeneratorError(probs)
    m2 = copy.copy(model)
    m2.lps = lps
    m2.summand_kind = kinds
    m2.user_maps = list(model.user_maps) + pmaps
    m2.walks = dict(model.walks, **pwalks)
    m2.fun_maps = dict(model.fun_maps, **pfun)
    indexes, watched = _indexes(lps, refined, model)
    return Stepper(m2, refined, inputs, outputs, indexes, watched)


def _indexes(lps, refined: Refined, model: Model):
    types = Types(lps, model.config.id_sorts)
    eqs = {}
    for e in lps.equations:
        h = e.lhs.head if isinstance(e.lhs, App) else e.lhs
        if isinstance(h, Op) and h.name.startswith("ix_"):
            eqs[h.name] = e
    out, watched = [], {}
    for name, info in refined.indexes.items():
        ic = IndexCode(name, info.sort, info.order, {})
        for part in ("mem", "cls", "adm", "key", "bound"):
            e = eqs.get("ix_%s_%s" % (part, name))
            if e is None:
                continue
            args = [a.name for a in e.lhs.args] if isinstance(e.lhs, App) else []
            ic.eqs[part] = (args, e.rhs)
        if info.cls_sort:
            ic.cls_sort = info.cls_sort
            ic.classes = [c for c, _, _ in lps.structs[info.cls_sort].constructors]
        ic.watched = list(dict.fromkeys(info.mem_args + info.cls_args + info.key_args))
        for p in ic.watched:
            watched[p] = info.sort
        out.append(ic)
    _ = types
    return out, watched
