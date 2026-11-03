"""The interface declaration, model/codegen.toml (docs/spec.md §3.6).

`read_config` checks the file's own shape (keys, types, allowed values) and
returns the declaration with the problems found; whether it matches the model
is checked in `frontend`. Every problem is error G6.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field

ROLES = ("config", "column", "table", "map", "frontier", "scalar")
WIDTHS = ("u8", "u16", "u32", "u64", "i32", "i64")
REPRS = ("bitset", "adj-fixed", "adj-append", "deque")
KINDS = ("input", "output")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_']*$")

_TOP = {"mcrl2_version", "parameter", "action", "id_sorts", "constants", "widths", "observable", "project", "systems", "cost_known", "bytes_per_id", "key_sorted"}
_PARAM = {"name", "role", "repr", "width", "sort", "init"}
_ACTION = {"label", "kind", "args"}
_OBS = {"name", "arg", "sort", "expr"}
_PROJECT_PATHS = {"model": "model", "generated": "generated", "catalogue": "catalogue", "properties": None}
_PROJECT = set(_PROJECT_PATHS) | {"backends", "rust_crate", "rust_package", "rust_workspace", "python_package",
                                   "typescript_module"}
BACKENDS = ("rust", "python", "typescript")
DEFAULT_BACKENDS = ["rust", "python"]  # what a project without `backends` was always generated
_SYSTEMS = {"groups", "ids"}
_SYSTEM_IDS = {"prefix", "header", "bound"}
_CRATE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_PYPKG = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TSMOD = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


@dataclass
class Config:
    mcrl2_version: str = ""
    parameters: list = field(default_factory=list)  # dicts: name, role, repr, width, sort
    actions: list = field(default_factory=list)  # dicts: label, kind, args
    id_sorts: list = field(default_factory=list)
    constants: dict = field(default_factory=dict)  # name -> sort text
    widths: dict = field(default_factory=dict)
    observables: list = field(default_factory=list)
    project: dict = field(default_factory=dict)  # the [project] table, defaults filled in (§3.6.9)
    systems: dict = field(default_factory=dict)  # the [systems] table: groups, ids (§3.17)
    cost_known: list = field(default_factory=list)  # "<summand>: <kind> <parameter>" sites G8 still lets through
    bytes_per_id: dict = field(default_factory=dict)  # id sort -> the limit of its per-id layout (G11, §3.13.4)
    key_sorted: dict = field(default_factory=dict)  # list column -> the key function it is kept sorted by (§3.10.7)


def read_config(text: str) -> tuple[Config, list[tuple[str, str, str]]]:
    """The declaration and its problems as (construct, section, message)."""
    problems: list[tuple[str, str, str]] = []
    cfg = Config()
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return cfg, [("file", "§3.6", "not valid TOML: %s" % e)]

    for k in sorted(set(data) - _TOP):
        problems.append((k, "§3.6", "unknown key %r" % k))

    bp = data.get("bytes_per_id", {})
    if not isinstance(bp, dict) or not all(isinstance(v, int) and v > 0 for v in bp.values()):
        problems.append(("bytes_per_id", "§3.13.4", "a table of id sorts to positive integers"))
    else:
        cfg.bytes_per_id = dict(bp)
    ks = data.get("key_sorted", {})
    if not isinstance(ks, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in ks.items()):
        problems.append(("key_sorted", "§3.10.7", "a table of list columns to key functions"))
    else:
        cfg.key_sorted = dict(ks)
    ck = data.get("cost_known", [])
    if not isinstance(ck, list) or not all(isinstance(x, str) for x in ck):
        problems.append(("cost_known", "§3.13.1", "a list of strings"))
    else:
        cfg.cost_known = list(ck)

    v = data.get("mcrl2_version")
    if not isinstance(v, str) or not v:
        problems.append(("mcrl2_version", "§3.6.1", "missing: the pinned toolset version"))
    else:
        cfg.mcrl2_version = v

    for i, p in enumerate(_tables(data, "parameter", problems)):
        where = "parameter %d (%s)" % (i + 1, p.get("name", "?"))
        for k in sorted(set(p) - _PARAM):
            problems.append((where, "§3.6.2", "unknown key %r" % k))
        if not isinstance(p.get("name"), str) or not _IDENT.match(p.get("name", "")):
            problems.append((where, "§3.6.2", "name missing or not an identifier"))
        if p.get("role") not in ROLES:
            problems.append((where, "§3.6.2", "role %r is not one of %s" % (p.get("role"), ", ".join(ROLES))))
        if "repr" in p and p["repr"] not in REPRS:
            problems.append((where, "§3.6.2", "repr %r is not one of %s" % (p["repr"], ", ".join(REPRS))))
        if "width" in p and p["width"] not in WIDTHS:
            problems.append((where, "§3.6.2", "width %r is not one of %s" % (p["width"], ", ".join(WIDTHS))))
        if "sort" in p and not isinstance(p["sort"], str):
            problems.append((where, "§3.6.2", "sort must be a string"))
        if "init" in p and (not isinstance(p["init"], str) or not p["init"].strip()):
            problems.append((where, "§3.6.2", "init must be an mCRL2 expression (a string)"))
            p = {k: v for k, v in p.items() if k != "init"}
        cfg.parameters.append(p)

    for i, a in enumerate(_tables(data, "action", problems)):
        where = "action %d (%s)" % (i + 1, a.get("label", "?"))
        for k in sorted(set(a) - _ACTION):
            problems.append((where, "§3.6.3", "unknown key %r" % k))
        if not isinstance(a.get("label"), str) or not _IDENT.match(a.get("label", "")):
            problems.append((where, "§3.6.3", "label missing or not an identifier"))
        if a.get("kind") not in KINDS:
            problems.append((where, "§3.6.3", "kind %r is not input or output" % (a.get("kind"),)))
        args = a.get("args")
        if not isinstance(args, list) or not all(isinstance(x, str) and _IDENT.match(x) for x in args):
            problems.append((where, "§3.6.3", "args must be a list of identifiers"))
            a = dict(a, args=[])
        elif len(set(args)) != len(args):
            problems.append((where, "§3.6.3", "args repeat a name"))
        cfg.actions.append(a)

    ids = data.get("id_sorts", [])
    if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
        problems.append(("id_sorts", "§3.6.4", "must be a list of sort names"))
    else:
        cfg.id_sorts = list(ids)

    consts = data.get("constants", {})
    if not isinstance(consts, dict) or not all(isinstance(v, str) for v in consts.values()):
        problems.append(("constants", "§3.6.5", "must be a table keyed by the constant maps' names, each value its sort"))
    else:
        cfg.constants = dict(consts)

    widths = data.get("widths", {})
    if not isinstance(widths, dict):
        problems.append(("widths", "§3.6.6", "must be a table sort = width"))
    else:
        for k, w in sorted(widths.items()):
            if w not in WIDTHS:
                problems.append(("widths.%s" % k, "§3.6.6", "width %r is not one of %s" % (w, ", ".join(WIDTHS))))
        cfg.widths = dict(widths)

    for i, o in enumerate(_tables(data, "observable", problems)):
        where = "observable %d (%s)" % (i + 1, o.get("name", "?"))
        for k in sorted(set(o) - _OBS):
            problems.append((where, "§3.6.7", "unknown key %r" % k))
        if not isinstance(o.get("name"), str) or not _IDENT.match(o.get("name", "")):
            problems.append((where, "§3.6.7", "name missing or not an identifier"))
        if not isinstance(o.get("expr"), str) or not o.get("expr", "").strip():
            problems.append((where, "§3.6.7", "expr missing"))
        if ("arg" in o) != ("sort" in o):
            problems.append((where, "§3.6.7", "an id argument needs both arg and sort"))
        cfg.observables.append(o)

    cfg.project = _project(data.get("project", {}), problems)
    cfg.systems = _systems(data.get("systems", {}), problems)
    return cfg, problems


def _project(t, problems: list) -> dict:
    """The [project] table (§3.6.9): where the model and the outputs are, the
    backends the project chooses, and the names of what each one generates. A
    missing name is a problem only when outputs are written (`missing_names`)."""
    out = dict(_PROJECT_PATHS, backends=list(DEFAULT_BACKENDS), rust_crate=None, rust_package=None,
               rust_workspace=False, python_package=None, typescript_module=None)
    if not isinstance(t, dict):
        problems.append(("project", "§3.6.9", "must be a table"))
        return out
    for k in sorted(set(t) - _PROJECT):
        problems.append(("project.%s" % k, "§3.6.9", "unknown key %r" % k))
    for k in _PROJECT_PATHS:
        if k in t:
            v = t[k]
            if not isinstance(v, str) or not v or v.startswith("/") or ".." in v.split("/") or "\\" in v:
                problems.append(("project.%s" % k, "§3.6.9", "must be a relative path inside the project"))
            else:
                out[k] = v.rstrip("/")
    for k, pat, what in (("rust_crate", _CRATE, "a crate name (letters, digits, _ and -)"),
                         ("rust_package", _CRATE, "a crate name (letters, digits, _ and -)"),
                         ("python_package", _PYPKG, "a Python identifier"),
                         ("typescript_module", _TSMOD, "a module name (letters, digits, _ and -)")):
        if k in t:
            if isinstance(t[k], str) and pat.match(t[k]):
                out[k] = t[k]
            else:
                problems.append(("project.%s" % k, "§3.6.9", "must be %s" % what))
    if "backends" in t:
        b = t["backends"]
        if not isinstance(b, list) or not b or not all(isinstance(x, str) and x in BACKENDS for x in b):
            problems.append(("project.backends", "§3.6.9", "must be a non-empty list of %s" % ", ".join(BACKENDS)))
        elif len(set(b)) != len(b):
            problems.append(("project.backends", "§3.6.9", "names a backend twice"))
        else:
            out["backends"] = list(b)
    if "rust_workspace" in t:
        if isinstance(t["rust_workspace"], bool):
            out["rust_workspace"] = t["rust_workspace"]
        else:
            problems.append(("project.rust_workspace", "§3.6.9", "must be true or false"))
    if out["rust_package"] is None:
        out["rust_package"] = out["rust_crate"]
    return out


def missing_names(cfg: Config) -> list[tuple[str, str, str]]:
    """The names the chosen backends' outputs need and the [project] table does not give (§3.6.9)."""
    chosen = cfg.project.get("backends", DEFAULT_BACKENDS)
    return [("project.%s" % k, "§3.6.9", "missing: %s" % why)
            for b, k, why in (("rust", "rust_crate", "the directory of the generated crate under <generated>/rust/"),
                              ("python", "python_package", "the name of the generated Python package"),
                              ("typescript", "typescript_module", "the name of the generated TypeScript module"))
            if b in chosen and not cfg.project.get(k)]


def _systems(t, problems: list) -> dict:
    """The [systems] table (§3.17): the instance groups and how id sorts are bounded."""
    out = {"groups": {}, "ids": {}}
    if not isinstance(t, dict):
        problems.append(("systems", "§3.17", "must be a table"))
        return out
    for k in sorted(set(t) - _SYSTEMS):
        problems.append(("systems.%s" % k, "§3.17", "unknown key %r" % k))
    g = t.get("groups", {})
    if not isinstance(g, dict) or not all(isinstance(v, list) and all(isinstance(x, str) for x in v) for v in g.values()):
        problems.append(("systems.groups", "§3.17", "must be a table: group name = [instance names]"))
    else:
        for k in g:
            if not _CRATE.match(k):
                problems.append(("systems.groups.%s" % k, "§3.17", "a group name is letters, digits, _ and -"))
        out["groups"] = {k: list(v) for k, v in g.items()}
    ids = t.get("ids", {})
    if not isinstance(ids, dict) or not all(isinstance(v, dict) for v in ids.values()):
        problems.append(("systems.ids", "§3.17", "must be a table of tables: [systems.ids.<sort>]"))
        return out
    for sort, d in ids.items():
        w = "systems.ids.%s" % sort
        for k in sorted(set(d) - _SYSTEM_IDS):
            problems.append((w, "§3.17", "unknown key %r" % k))
        if "prefix" in d and not (isinstance(d["prefix"], str) and _IDENT.match(d["prefix"])):
            problems.append((w, "§3.17", "prefix must be an identifier"))
        if "header" in d and not (isinstance(d["header"], str) and re.match(r"^[A-Za-z]\w*$", d["header"])):
            problems.append((w, "§3.17", "header must be a word"))
        if "bound" in d and not (isinstance(d["bound"], int) and not isinstance(d["bound"], bool) and d["bound"] > 0):
            problems.append((w, "§3.17", "bound must be a positive integer"))
        out["ids"][sort] = dict(d)
    return out


def _tables(data: dict, key: str, problems: list) -> list:
    v = data.get(key, [])
    if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
        problems.append((key, "§3.6", "%s must be an array of tables ([[%s]])" % (key, key)))
        return []
    return v
