"""The policy file `model/policy.toml` (docs/spec.md §3.7.1, §3.7.6) and its
checks (G9).

`read_policy` parses the file; `check_policy` judges it against Core's summands
and parameters as the source writes them. The terms stay mCRL2 text: mCRL2 types
them when the refined Core is linearised (`refine`).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field

from .errors import Diagnostic

POLICY = "model/policy.toml"
ORDERS = ("least", "key", "newest", "oldest")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_']*")
_OPS = re.compile(r"\b(least|first|some)\s*\(\s*([A-Za-z_][A-Za-z0-9_']*)\s*\)")


@dataclass
class Index:
    """An index of the policy (§3.7.6)."""

    name: str
    sort: str
    var: str
    bound: str
    member: str
    order: str
    key: str | None = None
    cls: str | None = None
    adm_var: str | None = None
    adm_when: str | None = None


@dataclass
class Policy:
    order: list = field(default_factory=list)  # output summand names, highest priority first
    select: dict = field(default_factory=dict)  # summand -> [(var, term, when)] in entry order
    index: dict = field(default_factory=dict)  # name -> Index, in file order
    maps: str = ""
    defer: dict = field(default_factory=dict)  # summand -> reason


def idents(text: str) -> list:
    """The identifiers of an mCRL2 term, in order of first appearance."""
    seen, out = set(), []
    for m in _IDENT.finditer(text):
        if m.group(0) not in seen:
            seen.add(m.group(0))
            out.append(m.group(0))
    return out


def operations(text: str) -> list:
    """The selection operations of a term: [(op, argument)] (§3.7.6)."""
    return [(m.group(1), m.group(2)) for m in _OPS.finditer(text)]


def read_policy(text: str):
    """(Policy, problems): problems are (where, section, message) for G9."""
    problems = []
    try:
        d = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return Policy(), [("file", "§3.7.1", "not TOML: %s" % e)]
    p = Policy()
    for k in d:
        if k not in ("order", "select", "index", "maps", "defer"):
            problems.append((k, "§3.7.1", "unknown key"))
    p.order = list(d.get("order", []))
    for s, tab in d.get("select", {}).items():
        entries = []
        for v, sel in tab.items():
            if not isinstance(sel, dict) or "term" not in sel:
                problems.append(("select.%s.%s" % (s, v), "§3.7.1", "a selector is { term = ..., when = ... }"))
                continue
            entries.append((v, sel["term"], sel.get("when", "true")))
        p.select[s] = entries
    for name, ix in d.get("index", {}).items():
        w = "index.%s" % name
        miss = [k for k in ("sort", "var", "bound", "member", "order") if k not in ix]
        if miss:
            problems.append((w, "§3.7.6", "missing %s" % ", ".join(miss)))
            continue
        if ix["order"] not in ORDERS:
            problems.append((w, "§3.7.6", "order %s is not one of %s" % (ix["order"], ", ".join(ORDERS))))
        if (ix["order"] == "key") != ("key" in ix):
            problems.append((w, "§3.7.6", "a key is given exactly when the order is key"))
        if ("class" in ix) != ("admits" in ix):
            problems.append((w, "§3.7.6", "class and admits come together"))
        adm = ix.get("admits") or {}
        p.index[name] = Index(name, ix["sort"], ix["var"], ix["bound"], ix["member"], ix["order"], ix.get("key"),
                              ix.get("class"), adm.get("var"), adm.get("when"))
    p.maps = d.get("maps", "")
    p.defer = dict(d.get("defer", {}))
    return p, problems


def check_policy(p: Policy, outputs: dict, params: dict, columns: set, sets: set, ids: dict, model_names: set) -> list:
    """G9 diagnostics. outputs: summand name -> (free sum variables, fixed sum variables);
    params: name -> sort text; columns: column names; sets: FSet parameters; ids: id sort -> constructor."""
    out = []

    def g9(where, section, msg):
        out.append(Diagnostic("G9", POLICY, where, section, msg))

    # order: total over the output summands.
    seen = set()
    for s in p.order:
        if s in seen:
            g9("order", "§3.7.1", "%s listed twice" % s)
        elif s not in outputs:
            g9("order", "§3.7.1", "%s is not an output summand" % s)
        seen.add(s)
    for s in outputs:
        if s not in seen:
            g9("order", "§3.7.1", "output summand %s missing" % s)

    # selectors: one per free variable, none for anything else.
    for s, (free, fixed) in outputs.items():
        given = [v for v, _, _ in p.select.get(s, [])]
        for v in free:
            if v not in given:
                g9("select.%s" % s, "§3.7.1", "no selector for the free sum variable %s" % v)
        for v in given:
            if v in fixed:
                g9("select.%s.%s" % (s, v), "§3.7.1", "%s is fixed by an equality" % v)
            elif v not in free:
                g9("select.%s.%s" % (s, v), "§3.7.1", "the summand has no sum variable %s" % v)
    for s in p.select:
        if s not in outputs:
            g9("select.%s" % s, "§3.7.1", "not an output summand")

    # operations: least over an FSet of an id sort, first/some over a declared index.
    for s, entries in p.select.items():
        for v, term, when in entries:
            for op, a in operations(term) + operations(when):
                if op == "least" and a not in sets:
                    g9("select.%s.%s" % (s, v), "§3.7.6", "least(%s): not an FSet parameter of an id sort" % a)
                if op in ("first", "some") and a not in p.index:
                    g9("select.%s.%s" % (s, v), "§3.7.6", "%s(%s): no such index" % (op, a))

    # indexes: locality (§3.7.6).
    for name, ix in p.index.items():
        w = "index.%s" % name
        if ix.sort not in ids:
            g9(w, "§3.7.6", "sort %s is not an id sort" % ix.sort)
        for what, term in (("member", ix.member), ("class", ix.cls), ("key", ix.key)):
            if term is None:
                continue
            prob = _not_local(term, ix.var, params, columns, sets)
            if prob:
                g9(w, "§3.7.6", "%s is not local: %s" % (what, prob))
        if ix.adm_when is not None:
            for x in idents(ix.adm_when):
                if x in columns:
                    g9(w, "§3.7.6", "admits reads the column %s" % x)
        for x in idents(ix.bound):
            if x in columns:
                g9(w, "§3.7.6", "bound reads the column %s" % x)

    # maps and reserved names.
    for m in re.finditer(r"\bmap\b(.*?)(?=\bvar\b|\beqn\b|$)", p.maps, re.S):
        for decl in m.group(1).split(";"):
            names = decl.split(":")[0]
            for n in names.split(","):
                n = n.strip()
                if n and not n.startswith("p_"):
                    g9("maps", "§3.7.1", "the map %s does not start with p_" % n)
    for n in sorted(model_names):
        if n.startswith("ix_"):
            g9("model", "§3.7.4", "the name %s starts with ix_, reserved for the generator" % n)

    # deferrals.
    for s in p.defer:
        if s not in outputs:
            g9("defer.%s" % s, "§3.7.1", "not an output summand")
        elif not outputs[s][0]:
            g9("defer.%s" % s, "§3.7.1", "a summand without a free sum variable cannot be deferred")
    return out


def _not_local(term: str, var: str, params: dict, columns: set, sets: set) -> str | None:
    """Why a term is not local (§3.7.6), or None: it reads columns at the member
    variable only, frontier sets by membership of the member variable, constants."""
    for m in _IDENT.finditer(term):
        x = m.group(0)
        if x not in params:
            continue
        rest = term[m.end():]
        if x in columns:
            if not re.match(r"\s*\(\s*" + re.escape(var) + r"\s*\)", rest):
                return "the column %s read at another id" % x
        elif x in sets:
            before = term[:m.start()]
            if not re.search(r"\b" + re.escape(var) + r"\s+in\s*$", before):
                return "the set %s used other than by membership of %s" % (x, var)
        else:
            return "the parameter %s, neither a column nor a set" % x
    if re.search(r"\b(exists|forall|lambda)\b", term):
        return "a quantifier"
    return None
