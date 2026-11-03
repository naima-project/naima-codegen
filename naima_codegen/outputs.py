"""The generated files and their manifest (docs/spec.md §3.1.2, §3.15).

`build` returns every file under the generated directory as {relative path:
text}, a function of the inputs (§3.1.1), the generator's own source and the
toolset version only (§3.15.1). `write` puts them in place through a temporary
directory, so that the generated directory is left as it was on any error
(§3.14.1); `diff` is the check of §3.15.3.

What is generated: the code of each backend the project chooses in its
[project] table, Rust, Python and TypeScript (the configuration, the initial
state, the user maps and walks, the acceptor and the entry codec, and, when the
model has a policy.toml, the stepper of the refined Core; for TypeScript also
the Svelte module), and with a policy the refined and probe Cores and the
systems of every group (§3.7.4, §3.9.7). The manifest's `cost` and `layout` stay null: the budgets of
§3.13 are not implemented.
"""

from __future__ import annotations

import hashlib
import re
import json
import shutil
import tempfile
from pathlib import Path

from . import layout, pyback, rustback, tsback
from .frontend import TOML



def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def generator_sources() -> dict:
    """{name: path} of the generator's own source, which the manifest hashes (§3.15.1)."""
    src = Path(__file__).resolve().parent
    out = {"naima_codegen/" + p.name: p for p in src.glob("*.py")}
    cli = src.parent / "naima-codegen"
    if cli.is_file():
        out["naima-codegen"] = cli
    return dict(sorted(out.items()))


def build(proj, model, toolset: str, tools=None) -> dict:
    """Every file under the project's generated directory, {relative path: text}."""
    from .config import missing_names
    from .errors import Diagnostic, GeneratorError

    names = missing_names(proj.cfg)
    if names:
        raise GeneratorError([Diagnostic("G6", proj.toml, w, sec, msg) for w, sec, msg in names])
    pj = proj.cfg.project
    policy = proj.m("policy.toml")
    st, model_files, systems_list = None, {}, []
    if proj.path(policy).is_file():
        try:
            st, model_files, systems_list = _refinement(proj, model, tools)
        except GeneratorError as e:
            raise proj.relocate(e) from None
    files = {}
    chosen = pj["backends"]
    if "python" in chosen:
        files["python/%s/__init__.py" % pj["python_package"]] = pyback.emit(model, st)
    if "rust" in chosen:
        for k, v in rustback.emit(model, st, package=pj["rust_package"], workspace=pj["rust_workspace"]).items():
            files["rust/%s/%s" % (pj["rust_crate"], k)] = v
    if "typescript" in chosen:
        for k, v in tsback.emit(model, st, module=pj["typescript_module"]).items():
            files["typescript/%s/%s" % (pj["typescript_module"], k)] = v
    files.update(model_files)
    costs = _whole_operations(proj, model, files["rust/%s/src/lib.rs" % proj.cfg.project["rust_crate"]])
    extra = [proj.m("env.mcrl2"), proj.m("observers.mcrl2")] + sorted(
        proj.m("instances/" + p.name) for p in proj.path(proj.m("instances")).glob("*.mcrl2")) if st is not None else []
    inputs = {f: _sha(proj.path(f).read_bytes())
              for f in [proj.m("core.mcrl2"), proj.m("codegen-init.mcrl2"), proj.toml, policy] + extra if proj.path(f).is_file()}
    lay = layout.layout(model, st)
    over = [(s, v["bytes"], model.config.bytes_per_id[s]) for s, v in lay["rust"].items()
            if s in model.config.bytes_per_id and v["bytes"] > model.config.bytes_per_id[s]]
    if over:
        from .errors import Diagnostic, GeneratorError
        raise GeneratorError([Diagnostic("G11", proj.toml, "bytes_per_id.%s" % s, "§3.13.4",
                                         "the per-id layout takes %d bytes, over the limit %d" % (b, lim))
                              for s, b, lim in over])
    manifest = {
        "generator": {k: _sha(p.read_bytes()) for k, p in generator_sources().items()},
        "toolset": toolset,
        "inputs": inputs,
        "summands": [{"name": s.name, "kind": model.summand_kind.get(s.name),
                      "policy": (st.refined.outputs.index(next(o for o in st.refined.outputs if o.name == s.name))
                                 if st is not None and model.summand_kind.get(s.name) == "output" else None),
                      "cost": costs.get(s.name, [])}
                     for s in model.lps.summands],
        "walks": sorted(model.walks),
        "layout": lay,
        "stepper": None if st is None else {
            "indexes": [{"name": ix.name, "sort": ix.sort, "order": ix.order, "classes": ix.classes, "reads": ix.watched}
                        for ix in st.indexes],
            "deferred": st.refined.deferred},
        "systems": systems_list,
        "files": {k: _sha(v.encode()) for k, v in sorted(files.items())},
    }
    files["MANIFEST.json"] = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    return files


def _whole_operations(proj, model, rust: str) -> dict:
    """The whole copies and scans of frontier sets and lists in the Rust step code
    (rustback.whole_operations); one not listed in `cost_known` of the declaration is
    G8, and so is a listed one that no longer happens (§3.13.1)."""
    from .errors import Diagnostic, GeneratorError
    from .functions import is_fun
    from .lower import Types

    types = Types(model.lps, model.config.id_sorts)
    frontier = {n for n, srt in model.lps.params if not is_fun(model.lps, srt) and types.kind(srt) in ("FSet", "List")}
    costs = rustback.whole_operations(rust, frontier)
    found = {"%s: %s" % (k, x) for k, v in costs.items() for x in v}
    known = set(model.config.cost_known)
    lib = "%s/rust/%s/src/lib.rs" % (proj.generated, proj.cfg.project["rust_crate"])
    probs = [Diagnostic("G8", lib, x, "§3.13.1",
                        "a whole frontier set or list copied or scanned at every firing; "
                        "change the model or the generator, or list it in cost_known of %s" % proj.toml)
             for x in sorted(found - known)]
    probs += [Diagnostic("G8", proj.toml, x, "§3.13.1", "listed in cost_known but no longer happens: remove it")
              for x in sorted(known - found)]
    if probs:
        raise GeneratorError(probs)
    return costs


def _refinement(proj, model, tools):
    """The refined Core, its stepper, and the files of generated/model (§3.7.4, §3.9.7)."""
    from . import stepper, systems
    from .errors import Diagnostic, GeneratorError
    from .policy import check_policy, read_policy
    from .refine import fixed_vars, read_core_text, refine
    from .toolchain import Toolchain

    tools = tools or Toolchain()
    core = proj.path(proj.m("core.mcrl2")).read_text(encoding="utf-8")
    pol, problems = read_policy(proj.path(proj.m("policy.toml")).read_text(encoding="utf-8"))
    diags = [Diagnostic("G9", "model/policy.toml", w, sec, msg) for w, sec, msg in problems]
    ct = read_core_text(core)
    outs = {}
    for s in ct.summands:
        if s.kind == "output":
            fixed = fixed_vars(s)
            outs[s.name] = ([n for n, _ in s.decls if n not in fixed], fixed)
    params = dict(ct.params)
    columns = {n for n, x in ct.params if "->" in x and x.split("->")[0].strip() in ct.ids}
    sets = {n for n, x in ct.params if re.fullmatch(r"FSet\((\w+)\)", x) and re.fullmatch(r"FSet\((\w+)\)", x).group(1) in ct.ids}
    names = {m.name for m in model.lps.maps} | set(params)
    diags += check_policy(pol, outs, params, columns, sets, ct.ids, names)
    if diags:
        raise GeneratorError(diags)
    ref = refine(core, pol, model.summand_kind)
    st = stepper.build(model, ref, core, proj.path(proj.m("codegen-init.mcrl2")).read_text(encoding="utf-8"), tools)
    files = {"model/core-refined.mcrl2": ref.refined, "model/core-probe.mcrl2": ref.probe, "model/probe.mcf": ref.probe_mcf}
    env = proj.path(proj.m("env.mcrl2")).read_text(encoding="utf-8")
    obs = proj.path(proj.m("observers.mcrl2")).read_text(encoding="utf-8")
    inst = {f.stem: f.read_text(encoding="utf-8") for f in sorted(proj.path(proj.m("instances")).glob("*.mcrl2"))}
    ids = systems.IdBounds(proj.cfg.id_sorts, proj.cfg.systems["ids"])
    try:
        grouped = systems.groups(inst, proj.cfg.systems["groups"])
    except ValueError as e:
        raise GeneratorError([Diagnostic("G6", TOML, "systems.groups", "§3.17", str(e))]) from None
    comm = []
    allow = (["probe_enabled", "probe_quiet", "probe_ambiguous"] + ["probe_deferred_%d" % k for k in range(len(ref.deferred))]
             + sorted({"%s|%s" % (v, mk) for v, mk, _ in ref.synced}))
    listed = []
    for g, members in grouped.items():
        ins = [(n, inst[n]) for n in members]
        try:
            systems.finite_ids(core, ids.bounds([t for _, t in ins]))
        except ValueError as e:
            raise GeneratorError([Diagnostic("G6", TOML, "systems, group %s" % g, "§3.17", str(e))]) from None
        files["model/systems/%s-model.mcrl2" % g] = systems.assemble(core, env, ins, ids)
        files["model/systems/%s-refined.mcrl2" % g] = systems.assemble(
            ref.refined, env, ins, ids, init_suffix=ref.init_suffix, observers=obs, quiescent=ref.quiescent)
        files["model/systems/%s-probe.mcrl2" % g] = systems.assemble(
            ref.probe, env, ins, ids, init_suffix=ref.init_suffix, comm=comm, allow=allow)
        listed.append({"group": g, "instances": members})
    return st, files, listed


def write(proj, files: dict) -> None:
    """Replace the generated directory by files, through a temporary directory (§3.14.1)."""
    dst = proj.gen_dir
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".generated-", dir=dst.parent))
    try:
        for k, v in files.items():
            p = tmp / k
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(v, encoding="utf-8", newline="\n")
        old = dst.parent / ".generated-old"
        if dst.exists():
            dst.rename(old)
        tmp.rename(dst)
        if old.exists():
            shutil.rmtree(old)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)


def diff(proj, files: dict) -> str | None:
    """The first difference between the generated directory and files, or None (§3.15.3)."""
    gen, GENERATED = proj.gen_dir, proj.generated
    have = {str(p.relative_to(gen)): p for p in gen.rglob("*") if p.is_file()} if gen.is_dir() else {}
    for k in sorted(set(have) | set(files)):
        if k not in have:
            return "%s/%s: missing" % (GENERATED, k)
        if k not in files:
            return "%s/%s: not generated (an extra file)" % (GENERATED, k)
        a, b = have[k].read_text(encoding="utf-8").split("\n"), files[k].split("\n")
        if a != b:
            n = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))
            return "%s/%s: differs at line %d" % (GENERATED, k, n + 1)
    return None
