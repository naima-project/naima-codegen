"""The generator's front end: read the linear process and model/codegen.toml,
refuse what is outside the accepted subset (docs/spec.md §3.1–§3.6, §3.14).

`check` judges inputs already read; `run` produces the LPS from the model with
the pinned toolset first (§3.2). Both collect every problem, then refuse with
one `GeneratorError` listing them by code.

The policy (G9, G13) and the construction assertions (G10) are checked in
policy.py, refine.py and stepper.py. Not implemented yet: the cost classes and
representations (G8, G11); only §3.4.8's G8 on two in-place updates that read
each other is checked here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config, read_config
from .errors import Diagnostic, GeneratorError
from .evaluate import NotEvaluable, evaluate
from .expr import App, Bind, Op, Var, free_vars, show, sort_parts, sort_str, walk
from .functions import calls_in_place, check_map_body, check_uses, is_fun, recognise_walk, ret_position
from .lps import Lps, read_lps
from .source import CoreSource, Entry, forbidden_in_core, read_core

CORE = "model/core.mcrl2"
INIT = "model/codegen-init.mcrl2"
TOML = "model/codegen.toml"
CATALOGUE = "catalogue"  # as the refusals name it; the directory is [project] catalogue (§3.6.9)

INIT_PREFIX = "gen_init_"  # the maps of init expressions the generator adds before linearising (§3.6.2)

SCALARS = {"Bool", "Pos", "Nat", "Int"}
COLLECTIONS = ("List", "FSet", "FBag")

# §3.4.1: built-in operators, by the names the LPS gives them.
BUILTINS = {
    "true", "false", "if", "==", "!=", "<", "<=", ">", ">=", "&&", "||", "!", "=>",
    "+", "-", "*", "div", "mod", "min", "max", "succ", "pred", "abs",
    "Int2Nat", "Nat2Pos", "Pos2Nat", "Nat2Int",
    "[]", "|>", "<|", "head", "tail", "rhead", "rtail", "#", ".", "++", "in",
    "{}", "@fset_empty", "@fset_cons", "@fset_insert", "@fset_union", "@fset_diff", "@fset_inter",
    "count", "@fbag_empty", "@fbag_cons", "@fbag_insert", "@func_update",
}


@dataclass
class Param:
    name: str
    sort: tuple
    role: str
    repr: str | None
    width: str | None
    init: object


@dataclass
class Action:
    label: str
    sorts: tuple
    kind: str
    args: list


@dataclass
class Model:
    """The front end's result: the accepted Core, ready for the policy and the backends."""

    lps: Lps
    config: Config
    params: list = field(default_factory=list)  # Param, LPS order
    actions: list = field(default_factory=list)  # Action
    user_maps: list = field(default_factory=list)  # MapDecl
    catalogue_maps: list = field(default_factory=list)  # MapDecl
    catalogue_entries: list = field(default_factory=list)  # names of the entries the model uses
    summand_kind: dict = field(default_factory=dict)  # summand name -> input or output
    walks: dict = field(default_factory=dict)  # map name -> Walk (§3.4.8)
    fun_maps: dict = field(default_factory=dict)  # map name -> (function-sorted argument positions, result position or None)
    in_place: dict = field(default_factory=dict)  # summand name -> [(parameter, map)]: p = f(..., p, ...) (§3.4.8)
    inits: dict = field(default_factory=dict)  # parameter name -> MapDecl of its init expression (§3.6.2)


class _Report:
    def __init__(self):
        self.items: list[Diagnostic] = []

    def add(self, code: str, file: str, where: str, section: str, message: str):
        self.items.append(Diagnostic(code, file, where, section, message))


def check(core_text: str, toml_text: str, aterm_text: str, catalogue: list[Entry] | None = None) -> Model:
    """The model the inputs describe, or GeneratorError with every refusal."""
    rep = _Report()
    src = read_core(core_text)
    lps = read_lps(aterm_text)
    cfg, problems = read_config(toml_text)
    for where, section, msg in problems:
        rep.add("G6", TOML, where, section, msg)

    struct_names = _struct_names(lps)
    cat_maps, cat_sorts, used_entries = _check_catalogue(lps, core_text, catalogue or [], rep)
    _check_sorts(lps, src, cat_sorts, rep)
    user_maps = [m for m in lps.maps if m.name not in cat_maps and not m.name.startswith(INIT_PREFIX)
                 and m.name not in struct_names]
    walks, fun_maps = _check_maps(lps, cfg, user_maps, rep)
    _check_core(lps, src, cfg, rep)
    for d in sumvar_clashes(src):
        rep.items.append(d)
    in_place = _check_expressions(lps, cfg, struct_names, {m.name for m in user_maps}, cat_maps, fun_maps, rep)
    _check_sum_vars(lps, struct_names, rep)
    model = _check_interface(lps, src, cfg, cat_sorts, rep)
    if rep.items:
        raise GeneratorError(rep.items)
    model.user_maps = user_maps
    model.walks, model.fun_maps, model.in_place = walks, fun_maps, in_place
    model.catalogue_maps = [m for m in lps.maps if m.name in cat_maps]
    model.catalogue_entries = used_entries
    return model


# ------------------------------------------------------------------ helpers


def _struct_names(lps: Lps) -> set:
    names = set()
    for s in lps.structs.values():
        for c, fields, rec in s.constructors:
            names.add(c)
            if rec:
                names.add(rec)
            for p, _ in fields:
                if p:
                    names.add(p)
    return names


def _resolve(lps: Lps, s: tuple) -> tuple:
    """s with aliases followed (aliases give no distinction, §3.3.7)."""
    seen = 0
    while s[0] == "id" and s[1] in lps.aliases and seen < 100:
        s = lps.aliases[s[1]]
        seen += 1
    return s


def _conjuncts(e) -> list:
    out, stack = [], [e]
    while stack:
        x = stack.pop()
        if isinstance(x, App) and isinstance(x.head, Op) and x.head.name == "&&" and len(x.args) == 2:
            stack.append(x.args[1])
            stack.append(x.args[0])
        else:
            out.append(x)
    return out


# --------------------------------------------------------------- catalogue


def _check_catalogue(lps: Lps, core_text: str, entries: list[Entry], rep: _Report):
    """§3.3.6, §3.10.1: an entry the model uses is copied byte for byte (G7)."""
    map_names = {m.name for m in lps.maps}
    sort_names = set(lps.structs) | set(lps.aliases)
    cat_maps, cat_sorts, used = set(), set(), []
    for e in entries:
        if e.text is None:
            continue
        if not (set(e.maps) & map_names or set(e.sorts) & sort_names):
            continue
        used.append(e.name)
        cat_maps |= set(e.maps)
        cat_sorts |= set(e.sorts)
        if e.text.rstrip("\n") not in core_text:
            rep.add("G7", CORE, "catalogue entry %s" % e.name, "§3.3.6",
                    "the model uses %s but does not contain %s/%s/reference.mcrl2 byte for byte"
                    % (", ".join(sorted((set(e.maps) & map_names) | (set(e.sorts) & sort_names))), CATALOGUE, e.name))
    return cat_maps, cat_sorts, used


# ------------------------------------------------------------------- sorts


def _sort_problem(lps: Lps, s: tuple, function_ok: bool) -> str | None:
    """Why s is outside §3.3, or None. function_ok: s may be `A -> B` at the top."""
    s = _resolve(lps, s)
    top = s
    for x in sort_parts(s):
        x = _resolve(lps, x)
        if x[0] == "id":
            if x[1] == "Real":
                return "Real (§3.3.1)"
            if x[1] not in SCALARS and x[1] not in lps.structs and x[1] not in lps.aliases:
                return "unknown sort %s" % x[1]
        elif x[0] in ("Set", "Bag"):
            return "%s, which may be infinite (§3.3.4)" % sort_str(x)
        elif x[0] in COLLECTIONS:
            el = _resolve(lps, x[1])
            if el[0] == "->" or el[0] in COLLECTIONS or el[0] in ("Set", "Bag"):
                return "%s: a collection of a function sort or a collection (§3.3.4)" % sort_str(x)
        elif x[0] == "->":
            if not (function_ok and x is top):
                return "function sort %s here (§3.3.5)" % sort_str(x)
            if len(x[1]) != 1:
                return "function sort %s over a product (§3.3.5)" % sort_str(x)
            for part in (x[1][0], x[2]):
                if _resolve(lps, part)[0] == "->":
                    return "function sort %s of a function sort (§3.3.5)" % sort_str(x)
        elif x[0] == "struct":
            return "an anonymous structured sort (§3.3.2)"
    return None


def _check_sorts(lps: Lps, src: CoreSource, cat_sorts: set, rep: _Report):
    # Structured sorts: fields, and no recursion (§3.3.2).
    graph = {}
    for name, st in sorted(lps.structs.items()):
        deps = set()
        for c, fields, _ in st.constructors:
            for p, fs in fields:
                why = _sort_problem(lps, fs, False)
                if why:
                    rep.add("G12", CORE, "sort %s, field %s of %s" % (name, p or "?", c), "§3.3", why)
                for part in sort_parts(fs):
                    part = _resolve(lps, part)
                    if part[0] == "id" and part[1] in lps.structs:
                        deps.add(part[1])
        graph[name] = deps
    for name in sorted(graph):
        if name in _reach(graph, name):
            rep.add("G12", CORE, "sort %s" % name, "§3.3.2", "a structured sort that contains itself")
    for name, s in sorted(lps.aliases.items()):
        why = _sort_problem(lps, s, False)
        if why:
            rep.add("G12", CORE, "sort %s" % name, "§3.3", why)
    for name, s in lps.params:
        why = _sort_problem(lps, s, True)
        if why:
            rep.add("G12", CORE, "parameter %s" % name, "§3.3", why)
    for a in lps.acts:
        for k, s in enumerate(a.sorts):
            why = _sort_problem(lps, s, False) or _action_arg_problem(lps, s)
            if why:
                rep.add("G12", CORE, "action %s, argument %d" % (a.name, k + 1), "§3.5.7", why)
    for s in lps.summands:
        for n, vs in s.sumvars:
            why = _sort_problem(lps, vs, False)
            if why:
                rep.add("G12", CORE, "summand %s, sum variable %s" % (s.name, n), "§3.3", why)
    for m in lps.maps:
        parts = list(m.sort[1]) + [m.sort[2]] if m.sort[0] == "->" else [m.sort]
        for p in parts:
            # Function sorts of maps: G3 for user maps (§3.4.2), allowed in catalogue maps.
            why = _sort_problem(lps, p, True)
            if why:
                rep.add("G12", CORE, "map %s" % m.name, "§3.3", why)


def _action_arg_problem(lps: Lps, s: tuple) -> str | None:
    """§3.5.7: scalars, id sorts, enumerations, records and unions whose fields are of
    these sorts or lists of them, nested to any finite depth, or lists of any of these."""
    s = _resolve(lps, s)
    if s[0] == "List":
        s = _resolve(lps, s[1])
    seen, stack = set(), [s]
    while stack:
        x = stack.pop()
        if x[0] != "id":
            return "%s: not a scalar, id sort, enumeration, record or union" % sort_str(x)
        if x[1] in SCALARS or x[1] in seen:
            continue
        seen.add(x[1])
        st = lps.structs.get(x[1])
        if st is None:
            continue  # an unknown sort is reported by _sort_problem
        for _, fields, _ in st.constructors:
            for _, fs in fields:
                fs = _resolve(lps, fs)
                if fs[0] == "List":
                    fs = _resolve(lps, fs[1])
                if fs[0] != "id":
                    return "%s has a field of sort %s" % (s[1] if s[0] == "id" else sort_str(s), sort_str(fs))
                stack.append(fs)
    return None


def _reach(graph: dict, start: str) -> set:
    seen, stack = set(), list(graph.get(start, ()))
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        stack.extend(graph.get(x, ()))
    return seen


# -------------------------------------------------------------------- maps


def _check_maps(lps: Lps, cfg: Config, user_maps: list, rep: _Report):
    """§3.4.2–§3.4.3, §3.4.8 (G3). Returns the walks and the function-sorted maps."""
    names = {m.name for m in user_maps}
    eqs: dict = {}
    for e in lps.equations:
        head = e.lhs.head if isinstance(e.lhs, App) else e.lhs
        if isinstance(head, Op) and head.name in names:
            eqs.setdefault(head.name, []).append(e)
    graph, walks, fun_maps = {}, {}, {}
    for m in user_maps:
        if m.arity:
            fpos = tuple(k for k, s in enumerate(m.sort[1]) if is_fun(lps, s))
            if fpos or is_fun(lps, m.sort[2]):
                fun_maps[m.name] = (fpos, None)
    for m in user_maps:
        where = "map %s" % m.name
        if m.arity == 0:
            if m.name not in cfg.constants:
                rep.add("G3", CORE, where, "§3.4.3", "a constant not listed under [constants] in %s" % TOML)
            elif cfg.constants[m.name].replace(" ", "") != sort_str(m.sort).replace(" ", ""):
                rep.add("G6", TOML, "constants.%s" % m.name, "§3.6.5",
                        "sort %s in %s, %s in the model" % (cfg.constants[m.name], TOML, sort_str(m.sort)))
        mine = eqs.get(m.name, [])
        if not mine:
            rep.add("G3", CORE, where, "§3.4.2", "no equation defines it")
        for e in mine:
            if not (isinstance(e.cond, Op) and e.cond.name == "true"):
                rep.add("G3", CORE, where, "§3.4.2", "a conditional equation: write the choice with if")
                break
        w, why = (recognise_walk(lps, m, mine) if m.arity else (None, None))
        if w is not None:
            walks[m.name] = w
        elif why:
            rep.add("G3", CORE, where, "§3.4.8", "not a walk: %s" % why)
        elif mine and m.arity and not _pattern_ok(lps, m, mine):
            rep.add("G3", CORE, where, "§3.4.2",
                    "equations are neither one over distinct variables, one per constructor of the first argument, nor a walk")
        if m.name in fun_maps:
            if is_fun(lps, m.sort[2]):
                k = w.ret_pos if w is not None else ret_position(lps, m, mine)
                if k is None:
                    rep.add("G3", CORE, where, "§3.4.8", "a function-sorted result not built from a function-sorted argument")
                fun_maps[m.name] = (fun_maps[m.name][0], k)
        graph[m.name] = {x.name for e in mine for x in walk(e.rhs) if isinstance(x, Op) and x.name in names} | {
            x.name for e in mine for x in walk(e.cond) if isinstance(x, Op) and x.name in names}
        if m.name in walks:
            graph[m.name].discard(m.name)  # a walk's call of itself (§3.4.2, rule 1)
    for m in user_maps:
        for why in sorted(set(check_map_body(lps, walks.get(m.name), m, eqs.get(m.name, []), fun_maps))):
            rep.add("G3", CORE, "map %s" % m.name, "§3.4.8", why)
    for n in sorted(graph):
        if n in _reach(graph, n):
            rep.add("G3", CORE, "map %s" % n, "§3.4.2", "a recursive definition")
    for c in cfg.constants:
        if not any(m.name == c and m.arity == 0 for m in user_maps):
            rep.add("G6", TOML, "constants.%s" % c, "§3.6.5", "not a constant map of the model")
    all_maps = {m.name for m in lps.maps}
    for e in lps.equations:
        head = e.lhs.head if isinstance(e.lhs, App) else e.lhs
        hn = head.name if isinstance(head, (Op, Var)) else show(head)
        if hn not in all_maps:
            rep.add("G3", CORE, "equation for %s" % hn, "§3.4.2", "an equation for an operator that is not a map of the model")
    return walks, fun_maps


def _distinct_vars(args) -> bool:
    return all(isinstance(a, Var) for a in args) and len({a.name for a in args}) == len(args)


def _pattern_ok(lps: Lps, m, eqs: list) -> bool:
    if len(eqs) == 1 and isinstance(eqs[0].lhs, App) and _distinct_vars(eqs[0].lhs.args):
        return True
    first = _resolve(lps, m.sort[1][0])
    st = lps.structs.get(first[1]) if first[0] == "id" else None
    if st is None or st.shape == "record":
        return False
    covered = []
    for e in eqs:
        if not isinstance(e.lhs, App):
            return False
        p, rest = e.lhs.args[0], e.lhs.args[1:]
        if isinstance(p, Op):
            c, pv = p.name, ()
        elif isinstance(p, App) and isinstance(p.head, Op):
            c, pv = p.head.name, p.args
        else:
            return False
        if not _distinct_vars(tuple(pv) + tuple(rest)):
            return False
        covered.append(c)
    return sorted(covered) == sorted(c for c, _, _ in st.constructors)


# -------------------------------------------------------------------- Core


def _check_core(lps: Lps, src: CoreSource, cfg: Config, rep: _Report):
    """§3.5.1–§3.5.2, §3.5.6 (G4)."""
    procs = src.decls["proc"]
    if "Core" not in procs:
        rep.add("G4", CORE, "proc Core", "§3.5.1", "no process Core")
    for p in procs:
        if p != "Core":
            rep.add("G4", CORE, "proc %s" % p, "§3.5.1", "a process other than Core")
    for what in forbidden_in_core(src):
        rep.add("G4", CORE, "proc Core", "§3.5.1", "%s in Core" % what)
    for s in lps.summands:
        if s.n_actions == 0:
            rep.add("G4", CORE, "summand %s" % s.name, "§3.5.1", "a summand without an action (tau)")
        elif s.n_actions > 1:
            rep.add("G4", CORE, "summand %s" % s.name, "§3.5.1", "a multi-action")
        if s.timed:
            rep.add("G4", CORE, "summand %s" % s.name, "§3.5.1", "time (@)")
        if s.n_actions and not s.label.startswith("c_"):
            rep.add("G4", CORE, "summand %s" % s.name, "§3.5.6", "label %s does not start with c_" % s.label)
    lps_labels = [s.label for s in lps.summands if s.n_actions]
    if src.labels and lps_labels != src.labels:
        k = next((i for i in range(min(len(src.labels), len(lps_labels))) if src.labels[i] != lps_labels[i]),
                 min(len(src.labels), len(lps_labels)))
        rep.add("G4", CORE, "proc Core", "§3.5.4",
                "the LPS does not list the summands in source order: summand %d is %s in the source, %s in the LPS"
                % (k + 1, src.labels[k] if k < len(src.labels) else "nothing", lps_labels[k] if k < len(lps_labels) else "nothing"))
    declared = [p.get("name") for p in cfg.parameters]
    actual = [n for n, _ in lps.params]
    if declared != actual:
        k = next((i for i in range(min(len(declared), len(actual))) if declared[i] != actual[i]), min(len(declared), len(actual)))
        got = "%s: %s" % (actual[k], sort_str(lps.params[k][1])) if k < len(actual) else "nothing"
        want = declared[k] if k < len(declared) else "nothing"
        rep.add("G4", CORE, "proc Core", "§3.5.2",
                "the LPS parameters differ from %s at position %d: the LPS has %s, the declaration %s (%d against %d parameters)"
                % (TOML, k + 1, got, want, len(actual), len(declared)))
    sorts = dict(lps.params)
    for p in cfg.parameters:
        if "sort" in p and p.get("name") in sorts and p["sort"].replace(" ", "") != sort_str(sorts[p["name"]]).replace(" ", ""):
            rep.add("G6", TOML, "parameter %s" % p["name"], "§3.6.2",
                    "sort %s in the LPS, %s in %s" % (sort_str(sorts[p["name"]]), p["sort"], TOML))


def sumvar_clashes(src: CoreSource) -> list:
    """§3.5.8 (G4): sum variables of Core named like a parameter, read from the source
    before linearising (mcrl22lps fails on them, or silently binds the parameter)."""
    params = {n for n, _ in src.params}
    return [Diagnostic("G4", CORE, "proc Core, sum variable %s" % n, "§3.5.8",
                       "a sum variable with the name of the parameter %s; rename it" % n)
            for n in sorted(src.sumvars & params)]


# ------------------------------------------------------------- expressions


def _check_expressions(lps: Lps, cfg: Config, struct_names: set, user: set, cat: set, fun_maps: dict, rep: _Report) -> dict:
    """§3.4.1, §3.4.4, §3.4.5 (G3): operators, lambdas and quantifiers in Core;
    §3.4.8: function-sorted values in Core (G3) and in-place updates (G8).
    Returns the in-place updates per summand."""
    allowed = BUILTINS | struct_names | user | cat
    columns = {n for n, s in lps.params if _is_column(lps, cfg, s)}
    fun_params = {n for n, s in lps.params if is_fun(lps, s)}
    psort = dict(lps.params)

    def ops(e, where):
        bad = sorted({x.name for x in walk(e) if isinstance(x, Op) and x.name not in allowed})
        for b in bad:
            rep.add("G3", CORE, where, "§3.4.1", "operator %s outside the accepted subset" % b)
        for x in walk(e):
            if isinstance(x, Op) and any(_resolve(lps, p) == ("id", "Real") for p in sort_parts(x.sort)):
                rep.add("G12", CORE, where, "§3.3.1", "operator %s over Real" % x.name)
                break

    def fun_uses(e, where):
        for why in sorted(set(check_uses(lps, e, fun_params, fun_maps))):
            rep.add("G3", CORE, where, "§3.4.8", why)

    in_place = {}
    for s in lps.summands:
        w = "summand %s" % s.name
        ops(s.cond, w + ", condition")
        fun_uses(s.cond, w + ", condition")
        for x in walk(s.cond):
            if isinstance(x, Bind):
                why = _quantifier_problem(x, columns)
                if why:
                    rep.add("G3", CORE, w + ", condition", "§3.4.4, §3.4.5", why)
        for k, a in enumerate(s.args):
            ops(a, "%s, argument %d" % (w, k + 1))
            fun_uses(a, "%s, argument %d" % (w, k + 1))
            _no_binders(a, "%s, argument %d" % (w, k + 1), rep)
        reads = {}
        for n, e in s.assigns:
            where = "%s, %s :=" % (w, n)
            ops(e, where)
            _no_binders(e, where, rep)
            if n not in psort or not is_fun(lps, psort[n]):
                fun_uses(e, where)
                continue
            for why in sorted(set(check_uses(lps, e, fun_params, fun_maps, n))):
                rep.add("G3", CORE, where, "§3.4.8", why)
            calls = calls_in_place(e, fun_maps)
            if calls:
                in_place.setdefault(s.name, []).append((n, calls))
                reads[n] = {x.name for x in walk(e) if isinstance(x, Var)} & fun_params - {n}
        for a in sorted(reads):
            for b in sorted(reads[a]):
                if a < b and b in reads and a in reads[b]:
                    rep.add("G8", CORE, w, "§3.4.8", "the in-place updates of %s and %s read each other's parameter" % (a, b))
    for (n, srt), v in zip(lps.params, lps.init):
        w = "initial value of %s" % n
        ops(v, w)
        body = v
        if isinstance(v, Bind) and v.kind == "Lambda":
            if _resolve(lps, srt)[0] != "->":
                rep.add("G3", INIT, w, "§3.4.4", "a lambda for a parameter that is not of a function sort")
            if free_vars(v.body) or any(isinstance(x, Var) and x.name in {b.name for b in v.vars} for x in walk(v.body)):
                rep.add("G3", INIT, w, "§3.4.4", "a lambda whose body is not a closed constant: %s" % show(v))
            body = v.body
        _no_binders(body, w, rep, INIT)
        free = free_vars(v) - {g.name for g in lps.globals}  # global variables: §3.2.5
        if free:
            rep.add("G3", INIT, w, "§3.1.1", "free variables %s" % ", ".join(sorted(free)))
    return in_place


def _no_binders(e, where: str, rep: _Report, file: str = CORE):
    for x in walk(e):
        if isinstance(x, Bind):
            what = "a lambda" if x.kind == "Lambda" else "a quantifier" if x.kind in ("Forall", "Exists") else "a %s" % x.kind
            rep.add("G3", file, where, "§3.4.4, §3.4.5", "%s outside a condition or an initial value: %s" % (what, show(x)[:120]))
            return


def _is_column(lps: Lps, cfg: Config, s: tuple) -> bool:
    s = _resolve(lps, s)
    return s[0] == "->" and len(s[1]) == 1 and _resolve(lps, s[1][0])[0] == "id" and _resolve(lps, s[1][0])[1] in cfg.id_sorts


def _quantifier_problem(b: Bind, columns: set) -> str | None:
    """§3.4.5: `exists x . x in col(n) && p(x)` or `forall x . x in col(n) => p(x)`."""
    if b.kind == "Lambda":
        return "a lambda in a condition: %s" % show(b)[:120]
    if b.kind not in ("Forall", "Exists"):
        return "a %s in a condition" % b.kind
    want = "=>" if b.kind == "Forall" else "&&"
    form = "forall x: X . x in col(n) => p(x)" if b.kind == "Forall" else "exists x: X . x in col(n) && p(x)"
    if len(b.vars) != 1:
        return "a quantifier over %d variables; write %s" % (len(b.vars), form)
    x = b.vars[0].name
    body = b.body
    if not (isinstance(body, App) and isinstance(body.head, Op) and body.head.name == want and len(body.args) == 2):
        return "a quantifier not of the form %s: %s" % (form, show(b)[:160])
    mem = body.args[0]
    if not (isinstance(mem, App) and isinstance(mem.head, Op) and mem.head.name == "in" and isinstance(mem.args[0], Var)
            and mem.args[0].name == x):
        return "a quantifier not bounded by membership, %s: %s" % (form, show(b)[:160])
    col = mem.args[1]
    if not (isinstance(col, App) and isinstance(col.head, Var) and col.head.name in columns and len(col.args) == 1
            and x not in free_vars(col.args[0])):
        return "a quantifier bounded by %s, which is not a per-node collection col(n) of a column" % show(col)
    return None


# ---------------------------------------------------------------- sum vars


def _check_sum_vars(lps: Lps, struct_names: set, rep: _Report):
    """§3.5.5 (G5): a whole action argument, a field at any depth of a constructor
    application that is an argument, or fixed by a conjunct x == e."""
    for s in lps.summands:
        bound, stack = set(), list(s.args)
        while stack:
            a = stack.pop()
            if isinstance(a, Var):
                bound.add(a.name)
            elif isinstance(a, App) and isinstance(a.head, Op) and a.head.name in struct_names:
                stack.extend(a.args)
        fixed = set()
        for c in _conjuncts(s.cond):
            if isinstance(c, App) and isinstance(c.head, Op) and c.head.name == "==" and len(c.args) == 2:
                for a, b in ((c.args[0], c.args[1]), (c.args[1], c.args[0])):
                    if isinstance(a, Var) and a.name not in free_vars(b):
                        fixed.add(a.name)
        for n, _ in s.sumvars:
            if n not in bound and n not in fixed:
                rep.add("G5", CORE, "summand %s" % s.name, "§3.5.5",
                        "sum variable %s is neither an action argument, a field of a constructor in one, "
                        "nor fixed by a conjunct %s == e" % (n, n))


# --------------------------------------------------------------- interface


def _check_interface(lps: Lps, src: CoreSource, cfg: Config, cat_sorts: set, rep: _Report) -> Model:
    """§3.6 (G6): the declaration against the model."""
    model = Model(lps, cfg)
    for name in cfg.id_sorts:
        st = lps.structs.get(name)
        if st is None or st.shape != "record" or len(st.constructors[0][1]) != 1 or _resolve(lps, st.constructors[0][1][0][1]) != ("id", "Nat"):
            rep.add("G6", TOML, "id_sorts", "§3.3.3, §3.6.4", "%s is not a record with one field of sort Nat" % name)
    ids = set(cfg.id_sorts)

    assigned = {n for s in lps.summands for n, _ in s.assigns}
    by_name = {p.get("name"): p for p in cfg.parameters}
    for (name, srt), init in zip(lps.params, lps.init):
        p = by_name.get(name)
        if p is None:
            continue
        role, repr_, width = p.get("role"), p.get("repr"), p.get("width")
        s = _resolve(lps, srt)
        w = "parameter %s" % name
        fun = s[0] == "->"
        col = fun and _resolve(lps, s[1][0])[0] == "id" and _resolve(lps, s[1][0])[1] in ids
        if role == "config":
            if name in assigned:
                rep.add("G6", TOML, w, "§3.6.2", "role config, but a summand assigns it")
            if fun:
                rep.add("G6", TOML, w, "§3.6.2", "role config for a function sort")
        if fun:
            dom = _resolve(lps, s[1][0])
            st = lps.structs.get(dom[1]) if dom[0] == "id" else None
            want = "column" if col else "table" if st is not None and st.shape == "enumeration" else "map"
            if role in ("config",):
                pass
            elif role != want:
                rep.add("G6", TOML, w, "§3.6.2", "%s is a function over %s: its role is %s, declared %s"
                        % (sort_str(s), sort_str(dom), want, role))
        elif role in ("column", "table", "map"):
            rep.add("G6", TOML, w, "§3.6.2", "role %s, but %s is not a function sort" % (role, sort_str(s)))
        if role == "frontier" and not (s[0] in COLLECTIONS or (s[0] == "id" and s[1] in cat_sorts)):
            rep.add("G6", TOML, w, "§3.6.2", "role frontier, but %s is not a collection" % sort_str(s))
        if repr_:
            target = _resolve(lps, s[2]) if col else s
            el = _resolve(lps, target[1]) if target[0] in COLLECTIONS else None
            id_el = el is not None and el[0] == "id" and el[1] in ids
            ok = (repr_ == "bitset" and not col and target[0] == "FSet" and id_el) or \
                 (repr_ == "adj-fixed" and col and target[0] == "List" and id_el) or \
                 (repr_ == "adj-append" and col and target[0] in ("List", "FSet") and id_el) or \
                 (repr_ == "deque" and not col and target[0] == "List")
            if not ok:
                rep.add("G6", TOML, w, "§3.6.2, §3.10.3", "repr %s does not fit %s with role %s" % (repr_, sort_str(s), role))
        if width:
            target = _resolve(lps, s[2]) if col else s
            if not _width_fits(target, width, ids):
                rep.add("G6", TOML, w, "§3.6.2", "width %s does not fit %s" % (width, sort_str(s)))
        if "init" in p:
            _check_init(lps, src, cfg, name, p["init"], role, model, rep)
        model.params.append(Param(name, srt, role, repr_, width, init))
    for k, w in sorted(cfg.widths.items()):
        if k in ("Nat", "Pos", "Int") or k in ids:
            if not _width_fits(("id", k), w, ids):
                rep.add("G6", TOML, "widths.%s" % k, "§3.6.6", "width %s does not fit %s" % (w, k))
        else:
            rep.add("G6", TOML, "widths.%s" % k, "§3.6.6", "%s is neither Nat, Pos, Int nor an id sort" % k)

    # Labels (§3.6.3): every label of Core exactly once, nothing else; an overloaded
    # label is told apart by its number of arguments.
    core_labels: dict = {}
    for s in lps.summands:
        if s.n_actions:  # a multi-action is G4; its first label is still Core's
            core_labels.setdefault((s.label, s.sorts), []).append(s)
    matched: dict = {}
    for a in cfg.actions:
        label, args = a.get("label"), a.get("args") or []
        cands = [k for k in core_labels if k[0] == label and len(k[1]) == len(args)]
        w = "action %s/%d" % (label, len(args))
        if not cands:
            other = sorted(len(k[1]) for k in core_labels if k[0] == label)
            rep.add("G6", TOML, w, "§3.6.3", "no summand of Core has this label with %d arguments%s"
                    % (len(args), " (it has %s)" % ", ".join(map(str, other)) if other else ""))
            continue
        if len(cands) > 1:
            rep.add("G6", TOML, w, "§3.6.3", "the label is overloaded with the same number of arguments")
            continue
        if cands[0] in matched:
            rep.add("G6", TOML, w, "§3.6.3", "declared twice")
            continue
        matched[cands[0]] = a
        model.actions.append(Action(label, cands[0][1], a.get("kind"), list(args)))
        for s in core_labels[cands[0]]:
            model.summand_kind[s.name] = a.get("kind")
    by_label: dict = {}
    for k in core_labels:
        by_label.setdefault(k[0], []).append(k)
    for label, sigs in sorted(by_label.items()):
        if len(sigs) < 2:
            continue
        kinds = sorted(matched[k].get("kind") for k in sigs if k in matched)
        if len(sigs) > 2 or len({len(k[1]) for k in sigs}) != len(sigs) or (len(kinds) == 2 and kinds != ["input", "output"]):
            rep.add("G6", TOML, "action %s" % label, "§3.6.3",
                    "an overloaded label must have two signatures of different arities, one input and one output")
    for k in sorted(core_labels, key=lambda k: (k[0], len(k[1]))):
        if k not in matched:
            sig = " # ".join(sort_str(x) for x in k[1]) or "no arguments"
            rep.add("G6", TOML, "action %s/%d" % (k[0], len(k[1])), "§3.6.3", "a label of Core (%s) with no [[action]]" % sig)

    # §3.6.8: the probe labels are the generator's, declared in the probe Core only.
    act_names = {a.name for a in lps.acts}
    probe = {"probe_enabled", "probe_quiet", "probe_ambiguous"}
    for a in model.actions:
        if a.kind == "input":
            probe |= {"c_%s_refused" % a.label[2:], "refused_%s" % a.label[2:]}
    used = {k[0] for k in core_labels}
    for nm in sorted(act_names & probe):
        rep.add("G6", CORE, "act %s" % nm, "§3.6.8", "a probe label, which the generator declares in the probe Core; remove it")
    for nm in sorted(act_names - used - probe):
        rep.add("G6", CORE, "act %s" % nm, "§3.1.1, §3.6.3", "declared, but no summand of Core performs it")

    for o in cfg.observables:
        if "sort" in o and o["sort"] not in ids:
            rep.add("G6", TOML, "observable %s" % o.get("name"), "§3.6.7", "argument sort %s is not an id sort" % o["sort"])
    return model


def _check_init(lps: Lps, src: CoreSource, cfg: Config, name: str, text: str, role: str, model: Model, rep: _Report):
    """§3.6.2: an init expression over configuration parameters, equal on the
    placeholder configuration to the placeholder value of codegen-init.mcrl2 (G6)."""
    w = "parameter %s, init" % name
    if role == "config":
        rep.add("G6", TOML, w, "§3.6.2", "a configuration parameter is given, not computed")
        return
    bad = init_refs(src, cfg, text)
    if bad:
        rep.add("G6", TOML, w, "§3.6.2", "mentions %s, which are not configuration parameters" % ", ".join(bad))
        return
    m = next((m for m in lps.maps if m.name == INIT_PREFIX + name), None)
    eq = [e for e in lps.equations if (e.lhs.head if isinstance(e.lhs, App) else e.lhs).name == INIT_PREFIX + name] if m else []
    if not eq:
        rep.add("G6", TOML, w, "§3.6.2", "the init expression is not in the linearised input (run through naima-codegen generate)")
        return
    model.inits[name] = m
    config = [n for n, _ in lps.params if any(p.get("name") == n and p.get("role") == "config" for p in cfg.parameters)]
    values = dict(zip([n for n, _ in lps.params], lps.init))
    try:
        env = {n: evaluate(values[n], {}, lps) for n in config}
        lhs = eq[0].lhs
        args = [a.name for a in lhs.args] if isinstance(lhs, App) else []
        got = evaluate(eq[0].rhs, {a: env[a] for a in args}, lps)
        want = evaluate(values[name], {}, lps)
    except (NotEvaluable, KeyError, ZeroDivisionError) as e:
        rep.add("G6", TOML, w, "§3.6.2", "cannot be evaluated on the placeholder configuration: %s" % e)
        return
    if got != want:
        rep.add("G6", TOML, w, "§3.6.2", "%r on the placeholder configuration, but %s has %r" % (got, INIT, want))


_IDENT = __import__("re").compile(r"[A-Za-z_][A-Za-z0-9_']*")


def init_refs(src: CoreSource, cfg: Config, text: str) -> list:
    """The Core parameters an init expression mentions that are not configuration parameters."""
    config = {p.get("name") for p in cfg.parameters if p.get("role") == "config"}
    params = {n for n, _ in src.params}
    return sorted({x for x in _IDENT.findall(text) if x in params and x not in config})


def linearisation_input(core: str, init: str, src: CoreSource, cfg: Config) -> str:
    """What is linearised (§3.1.1): model/core.mcrl2, the init maps, model/codegen-init.mcrl2."""
    return core + init_block(src, cfg) + init


def init_block(src: CoreSource, cfg: Config) -> str:
    """The maps of the init expressions (§3.6.2), appended to the model before
    linearising so that mCRL2 types them: `gen_init_p(config...) = expr`."""
    sorts = dict(src.params)
    config = [(n, sorts[n]) for n, _ in src.params
              if any(p.get("name") == n and p.get("role") == "config" for p in cfg.parameters)]
    out = []
    for p in cfg.parameters:
        n = p.get("name")
        if "init" not in p or n not in sorts or p.get("role") == "config" or init_refs(src, cfg, p["init"]):
            continue
        name = INIT_PREFIX + n
        if config:
            out.append("map %s: %s -> %s;\nvar %s;\neqn %s(%s) = %s;\n" % (
                name, " # ".join(s for _, s in config), sorts[n], "; ".join("%s: %s" % c for c in config),
                name, ", ".join(c for c, _ in config), p["init"]))
        else:
            out.append("map %s: %s;\neqn %s = %s;\n" % (name, sorts[n], name, p["init"]))
    return "".join(out)


def _width_fits(s: tuple, width: str, ids: set) -> bool:
    if s[0] != "id":
        return False
    if s[1] in ids:
        return width.startswith("u")
    if s[1] == "Int":
        return width in ("i32", "i64")
    if s[1] in ("Nat", "Pos"):
        return width.startswith("u")
    return False


# --------------------------------------------------------------------- run


def run(root, tools=None) -> Model:
    """Linearise the model of the project at root (a directory or a `project.Project`)
    with the pinned toolset and check it (§3.1.1, §3.2). File names in the
    refusals are the project's."""
    from .project import Project, load

    proj = root if isinstance(root, Project) else load(root)
    try:
        return _run(proj, tools)
    except GeneratorError as e:
        raise proj.relocate(e) from None


def _run(proj, tools) -> Model:
    from .source import read_catalogue
    from .toolchain import Toolchain, check_version

    tools = tools or Toolchain()
    texts, missing = {}, []
    for f, rel, code in ((CORE, proj.m("core.mcrl2"), "G4"), (INIT, proj.m("codegen-init.mcrl2"), "G4"), (TOML, proj.toml, "G6")):
        p = proj.path(rel)
        if p.is_file():
            texts[f] = p.read_text(encoding="utf-8")
        else:
            missing.append(Diagnostic(code, f, "file", "§3.1.1", "missing"))
    if missing:
        raise GeneratorError(missing)
    cfg, _ = read_config(texts[TOML])
    if not cfg.mcrl2_version:
        raise GeneratorError([Diagnostic("G6", TOML, "mcrl2_version", "§3.6.1", "missing: the pinned toolset version")])
    check_version(tools, cfg.mcrl2_version, TOML)
    src = read_core(texts[CORE])
    clashes = sumvar_clashes(src)  # §3.5.8: before linearising, which would fail on them (G2)
    if clashes:
        raise GeneratorError(clashes)
    core = texts[CORE] if texts[CORE].endswith("\n") else texts[CORE] + "\n"
    aterm = tools.linearise(linearisation_input(core, texts[INIT], src, cfg), CORE)
    return check(texts[CORE], texts[TOML], aterm, read_catalogue(proj.catalogue))
