"""The command line, `naima-codegen` (docs/spec.md §3.6.9).

    naima-codegen generate [--check | --front-end] [--project DIR]
    naima-codegen check-refinement [--properties | --no-model] [--project DIR] [group...]
    naima-codegen lean --out DIR [--namespace NAME] [--free P,...] [--project DIR]

generate: linearise the model with the pinned toolset, refuse what is outside
the accepted subset listing every refusal with its code, then write the code of
each backend the project chooses (Rust, Python, TypeScript) and the
refinement's systems under the generated directory (§3.1.2). --check regenerates and compares with what is there
(§3.15.3); --front-end only judges the model (§3.1-§3.6, §3.14.1).

check-refinement: the checks that the refinement stays inside the model
(§3.9), on the systems `generate` wrote under <generated>/model/systems. For
each group (default: every group), linearise and explore the three systems
(mcrl22lps, lps2lts), then on their finite state spaces:

- §3.9.2 trace inclusion: ltscompare --preorder=trace-ac refined model, the
  observers' labels hidden (--no-model skips it: the model system, Core
  without the step structure, can be too large to explore);
- §3.9.3 non-blocking: no state of the probe system with both a probe_enabled
  and a probe_quiet transition; the states where a deferred summand is deferred
  are counted and reported;
- §3.9.5 every step ends: no cycle of output transitions in the refined system;
- §3.9.6 one summand per label: no probe_ambiguous transition, no input taken
  with probe_ambiguous_in;
- §3.9.7 receptive: no input taken with probe_refused;
- the observers (o_*) of the model's observers.mcrl2 never fire on the refined system.

With --properties it runs §3.9.4 instead (lps2pbes, pbessolve): every property
item under [project] properties whose model is <model>/systems/<group>.mcrl2,
on the group's refined system. Prints one line per check; exit status 1 when
any fails. Long on large models: run detached with a log.

lean: the refined Core with its observers, linearised, as a Lean 4 transition
system (leanback.py), written to DIR as Mcrl2.lean (mCRL2's library) and
NAME.lean (default Model), with each property of the form [true* . a] false
as a proposition prop_<name> to prove. The configuration parameters, and those
named by --free, are left free in the initial state.

The project is the directory DIR (default: the current directory); its
codegen.toml says where the model and the outputs are (§3.6.9).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from .errors import GeneratorError

USAGE = """usage: naima-codegen generate [--check | --front-end] [--project DIR]
       naima-codegen check-refinement [--properties | --no-model] [--project DIR] [group...]
       naima-codegen lean --out DIR [--namespace NAME] [--free P,...] [--project DIR]"""


def _project_arg(argv: list) -> tuple[Path, list] | None:
    out, root, k = [], Path("."), 0
    while k < len(argv):
        a = argv[k]
        if a == "--project":
            if k + 1 >= len(argv):
                return None
            root, k = Path(argv[k + 1]), k + 2
            continue
        if a.startswith("--project="):
            root = Path(a.split("=", 1)[1])
        else:
            out.append(a)
        k += 1
    return root, out


def _refused(cmd: str, e: GeneratorError) -> int:
    print("naima-codegen %s: refused, %d problems" % (cmd, len(e.diagnostics)), file=sys.stderr)
    for d in e.diagnostics:
        print(d, file=sys.stderr)
    return 1


def generate(root: Path, argv: list) -> int:
    from . import outputs
    from .frontend import run
    from .project import load
    from .toolchain import Toolchain

    if argv not in ([], ["--check"], ["--front-end"]):
        print(USAGE, file=sys.stderr)
        return 2
    try:
        proj = load(root)
        model = run(proj)
        if argv == ["--front-end"]:
            lps = model.lps
            print("naima-codegen generate: accepted: %d parameters, %d summands (%d input, %d output), %d user maps"
                  % (len(lps.params), len(lps.summands), sum(1 for k in model.summand_kind.values() if k == "input"),
                     sum(1 for k in model.summand_kind.values() if k == "output"), len(model.user_maps)))
            return 0
        files = outputs.build(proj, model, Toolchain().version())
    except GeneratorError as e:
        return _refused("generate", e)
    if argv == ["--check"]:
        d = outputs.diff(proj, files)
        if d:
            print("naima-codegen generate --check: %s; run naima-codegen generate" % d, file=sys.stderr)
            return 1
        print("naima-codegen generate --check: %s/ is up to date" % proj.generated)
        return 0
    outputs.write(proj, files)
    print("naima-codegen generate: wrote %d files under %s/" % (len(files), proj.generated))
    return 0


def check_refinement(root: Path, argv: list) -> int:
    from .checks import check, verify_properties
    from .project import load

    if any(a.startswith("-") and a not in ("--properties", "--no-model") for a in argv):
        print(USAGE, file=sys.stderr)
        return 2
    try:
        proj = load(root)
    except GeneratorError as e:
        return _refused("check-refinement", e)
    sysdir = proj.gen_dir / "model" / "systems"
    props = "--properties" in argv
    model = "--no-model" not in argv
    argv = [a for a in argv if a not in ("--properties", "--no-model")]
    groups = argv or sorted({p.name.rsplit("-", 1)[0] for p in sysdir.glob("*-probe.mcrl2")})
    if not groups:
        print("check-refinement: no systems under %s; run naima-codegen generate" % sysdir, flush=True)
        return 1
    ok = True
    with tempfile.TemporaryDirectory(prefix="naima-refinement-") as d:
        for g in groups:
            try:
                say = lambda *a: print(*a, flush=True)  # noqa: E731
                ok &= verify_properties(proj, g, Path(d), say) if props else check(proj, g, Path(d), say, model)
            except RuntimeError as e:
                print("%s: %s" % (g, e), flush=True)
                ok = False
    print("check-refinement: %s" % ("every check holds" if ok else "FAILED"))
    return 0 if ok else 1


def lean(root: Path, argv: list) -> int:
    from .leanback import build
    from .project import load

    opts = {}
    while argv:
        if argv[0] not in ("--out", "--namespace", "--free") or len(argv) < 2:
            print(USAGE, file=sys.stderr)
            return 2
        opts[argv[0]] = argv[1]
        argv = argv[2:]
    if "--out" not in opts:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        files = build(load(root), [x for x in opts.get("--free", "").split(",") if x], opts.get("--namespace", "Model"))
    except GeneratorError as e:
        return _refused("lean", e)
    out = Path(opts["--out"])
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
    print("naima-codegen lean: wrote %s under %s/" % (", ".join(sorted(files)), out))
    return 0


def main(argv: list | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] in (["-h"], ["--help"]):
        print(__doc__.strip())
        return 0
    parsed = _project_arg(argv[1:]) if argv else None
    if parsed is None or argv[0] not in ("generate", "check-refinement", "lean"):
        print(USAGE, file=sys.stderr)
        return 2
    root, rest = parsed
    return {"generate": generate, "check-refinement": check_refinement, "lean": lean}[argv[0]](root, rest)
