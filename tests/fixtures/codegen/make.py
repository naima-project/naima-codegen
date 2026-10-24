#!/usr/bin/env python3
"""Regenerate the generator front end's fixtures (docs/spec.md §3.2.2).

The tests read the LPS from files kept here, so they neither need mCRL2 nor
depend on the model while it is being edited. This script makes them with the
pinned commands (`-n`: no clustering), on the input the generator linearises
(core, the maps of the `init` expressions, codegen-init; §3.1.1, §3.6.2):

- mini/lps.aterm: the small Core inside the subset (mini/*);
- cases/<name>/: one negative case per refusal, mini with the edits of CASES
  below; `expect` holds the codes the front end must give, and `lps.aterm` the
  LPS when the case changes what is linearised (a case that does not uses
  mini's; a case mcrl22lps refuses has none);
- jobqueue/lps.aterm: the example model, examples/jobqueue (its model/core.mcrl2,
  model/codegen-init.mcrl2 and codegen.toml).

With --snapshots it also rewrites the golden snapshots mini/expected.txt and
jobqueue/expected.txt from the current front end: review their diff.
Usage: make.py [--snapshots]   (run by tests/test_codegen_frontend.py in check
mode: it regenerates into a temporary directory and compares).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
EXAMPLE = ROOT / "examples" / "jobqueue"
EXAMPLE_FILES = ("model/core.mcrl2", "model/codegen-init.mcrl2", "codegen.toml")

PIN = "202607.0"

# name -> (expected codes, edits). Edits: {"core"|"init"|"toml": [(old, new)], "catalogue": {entry: text}}.
_CAT_TEXT = "map  cap: Nat # Nat -> Nat;\nvar  c1, c2: Nat;\neqn  cap(c1, c2) = if(c1 > c2, c2, c1);\n"
_CORE_CAT = [("     cap: Nat # Nat -> Nat;\n", ""), ("     cap(a, b) = if(a > b, b, a);\n", ""), ("act  c_submit", _CAT_TEXT + "act  c_submit")]
_BUMP_IF = ("     tick: (St -> Nat) # St -> (St -> Nat);                % a function-sorted result, not a walk\n",
            "     tick: (St -> Nat) # St -> (St -> Nat);\n"
            "     bumpIf: (Node -> Nat) # List(Node) # (Node -> St) -> (Node -> Nat);\n"
            "     stIf: (Node -> St) # List(Node) # (Node -> Nat) -> (Node -> St);\n")
_BUMP_IF_EQ = ("     tick(t, s) = t[s -> t(s) + 1];\n",
               "     tick(t, s) = t[s -> t(s) + 1];\n"
               "     bumpIf(f, [], g) = f;\n     bumpIf(f, n |> l, g) = bumpIf(if(g(n) == BUSY, f[n -> f(n) + 1], f), l, g);\n"
               "     stIf(g, [], f) = g;\n     stIf(g, n |> l, f) = stIf(if(f(n) > 0, g[n -> DONE], g), l, f);\n")
CASES = {
    "ok-catalogue": ("", {"core": _CORE_CAT, "catalogue": {"capacity": _CAT_TEXT}}),
    "g3-recursive-map": ("G3", {"core": [("     isFail: Why -> Bool;\n", "     isFail: Why -> Bool;\n     sumto: Nat -> Nat;\n"),
                                         ("     cap(a, b) =", "     sumto(a) = if(a == 0, 0, a + sumto(Int2Nat(a - 1)));\n     cap(a, b) =")]}),
    "g3-conditional-equation": ("G3", {"core": [("     isFail: Why -> Bool;\n", "     isFail: Why -> Bool;\n     choose: Nat -> Nat;\n"),
                                                ("     cap(a, b) =", "     a > 2 -> choose(a) = 2;\n     a <= 2 -> choose(a) = a;\n     cap(a, b) =")]}),
    "g3-constant-not-listed": ("G3", {"toml": [('[constants]\nlim = "Nat"\n', "")]}),
    "g3-quantifier-over-frontier": ("G3", {"core": [("forall y: Node . y in deps(x) =>", "forall y: Node . y in RQ =>")]}),
    "g3-lambda-in-assignment": ("G3", {"core": [("c_done(x) . Core(st = st[x -> next(st(x))],", "c_done(x) . Core(st = lambda z: Node . DONE,")]}),
    "g3-operator-outside-subset": ("G3", {"core": [("run < cap(w, lim)", "run < exp(2, w)")]}),
    "g3-function-result-of-two": ("G3", {"core": [("     isFail: Why -> Bool;\n", "     isFail: Why -> Bool;\n     choose2: (Node -> Nat) # (Node -> Nat) # Bool -> (Node -> Nat);\n"),
                                                  ("var  s: St;", "var  h: Node -> Nat; d: Bool; s: St;"),
                                                  ("     cap(a, b) =", "     choose2(f, h, d) = if(d, f, h);\n     cap(a, b) =")]}),
    "g3-walk-two-operators": ("G3", {"core": [("g(n) == BUSY || anyBusy(l, g)", "g(n) == BUSY || (g(n) == IDLE && anyBusy(l, g))")]}),
    "g3-in-place-result-to-another-parameter": ("G3", {"core": [("slots: Nat) =", "slots: Nat, used: Node -> Nat) ="),
                                                                ("uses = bump(uses, l))", "used = bump(uses, l))")],
                                                       "init": [("2 * 2);", "2 * 2, lambda x: Node . 0);")],
                                                       "toml": [('[[action]]\nlabel = "c_submit"', '[[parameter]]\nname = "used"\nrole = "column"\n\n[[action]]\nlabel = "c_submit"')]}),
    "g3-open-lambda": ("G3", {"init": [("lambda x: Node . IDLE", "lambda x: Node . if(ix(x) == 0, BUSY, IDLE)")]}),
    "g4-other-process": ("G4", {"core": [("     + delta;\n", "     + delta;\nproc Aux = c_finish(0) . Aux;\n")]}),
    "g4-program-counter": ("G4", {"core": [("c_done(x) . Core(", "c_done(x) . c_finish(0) . Core(")]}),
    "g4-multi-action": ("G4", {"core": [("-> c_done(x) . Core(", "-> c_done(x) | c_finish(0) . Core(")]}),
    "g4-tau": ("G4", {"core": [("     + delta;\n", "     + (n > 7) -> tau . Core()\n     + delta;\n")]}),
    "g4-parameters-differ": ("G4", {"toml": [('[[parameter]]\nname = "w"\nrole = "config"\n\n[[parameter]]\nname = "n"\nrole = "scalar"\n',
                                              '[[parameter]]\nname = "n"\nrole = "scalar"\n\n[[parameter]]\nname = "w"\nrole = "config"\n')]}),
    "g4-label-prefix": ("G4", {"core": [("c_finish", "finish")], "toml": [('label = "c_finish"', 'label = "finish"')]}),
    "g4-sum-variable-names-parameter": ("G4", {"core": [("sum x: Node . (x in RQ && run == 0 && deps(x) == []) -> c_fire(x, 0)",
                                                         "sum x: Node, run: Nat . (x in RQ && run == 0 && deps(x) == []) -> c_fire(x, run)")]}),
    "g4-sum-variable-shadows-parameter": ("G4", {"core": [("sum x: Node . (st(x) == BUSY && run > 0) -> c_done(x)",
                                                           "sum x: Node, w: Bool . (st(x) == BUSY && run > 0 && w) -> c_done(x)")]}),
    "g5-free-sum-variable": ("G5", {"core": [("sum x: Node . (st(x) == BUSY && run > 0)", "sum x: Node, k: Nat . (st(x) == BUSY && run > k)")]}),
    "g5-not-a-constructor-field": ("G5", {"core": [("sum y: Why . (run == 0) -> c_halt(y)", "sum y: Node . (run == 0) -> c_halt(if(ix(y) > 0, wfail(y), wnone))"),
                                                   ("Core(why = y)", "Core(why = wnone)")]}),
    "g6-config-assigned": ("G6", {"toml": [('name = "n"\nrole = "scalar"', 'name = "n"\nrole = "config"')]}),
    "g6-label-not-declared": ("G6", {"toml": [('[[action]]\nlabel = "c_done"\nkind = "input"\nargs = ["node"]\n', "")]}),
    "g6-unknown-label": ("G6", {"toml": [('[[observable]]\nname = "running"', '[[action]]\nlabel = "c_nothing"\nkind = "output"\nargs = []\n\n[[observable]]\nname = "running"')]}),
    "g6-repr-does-not-fit": ("G6", {"toml": [('repr = "adj-fixed"', 'repr = "bitset"')]}),
    "g6-probe-label-declared": ("G6", {"core": [("     c_halt: Why;\n", "     c_halt: Why;\n     c_done_refused, refused_done: Node;\n")]}),
    "g6-act-not-used": ("G6", {"core": [("     c_halt: Why;\n", "     c_halt: Why;\n     c_spare: Nat;\n")]}),
    "g6-id-sort-not-a-record": ("G6", {"toml": [('id_sorts = ["Node"]', 'id_sorts = ["Node", "St"]')]}),
    "g6-unknown-key": ("G6", {"toml": [('id_sorts = ["Node"]', 'id_sorts = ["Node"]\ncolour = "blue"')]}),
    "g6-role-does-not-match-domain": ("G6", {"toml": [('name = "tally"\nrole = "table"', 'name = "tally"\nrole = "map"')]}),
    "g6-init-differs-from-placeholder": ("G6", {"toml": [('init = "2 * w"', 'init = "3 * w"')]}),
    "g6-init-not-over-configuration": ("G6", {"toml": [('init = "2 * w"', 'init = "run + 4"')]}),
    "g6-constant-sort": ("G6", {"toml": [('lim = "Nat"', 'lim = "Pos"')]}),
    "g6-overloaded-same-kind": ("G6", {"core": [("     c_halt: Why;\n", "     c_halt: Why;\n     c_finish: Nat # Nat;\n"),
                                                ("     + delta;\n", "     + (n == 1) -> c_finish(n, n) . Core()\n     + delta;\n")],
                                       "toml": [('[[action]]\nlabel = "c_halt"', '[[action]]\nlabel = "c_finish"\nkind = "output"\nargs = ["a", "b"]\n\n[[action]]\nlabel = "c_halt"')]}),
    "g7-catalogue-text-differs": ("G7", {"core": _CORE_CAT, "catalogue": {"capacity": _CAT_TEXT.replace("c1 > c2", "c1 >= c2")}}),
    "g8-in-place-updates-read-each-other": ("G8", {"core": [_BUMP_IF, _BUMP_IF_EQ,
                                                            ("uses = bump(uses, l))", "uses = bumpIf(uses, l, st), st = stIf(st, l, uses))")]}),
    "g12-real": ("G12", {"core": [("slots: Nat) =", "slots: Nat, r: Real) =")],
                         "init": [("2 * 2);", "2 * 2, 0);")],
                         "toml": [('[[action]]\nlabel = "c_submit"', '[[parameter]]\nname = "r"\nrole = "scalar"\n\n[[action]]\nlabel = "c_submit"')]}),
    "g12-set": ("G12", {"core": [("     isFail: Why -> Bool;\n", "     isFail: Why -> Bool;\n     sset: Set(Node) -> Bool;\n"),
                                 ("var  s: St;", "var  ss: Set(Node); s: St;"),
                                 ("     cap(a, b) =", "     sset(ss) = true;\n     cap(a, b) =")]}),
    "g12-recursive-sort": ("G12", {"core": [("     Why = struct", "     Tree = struct leaf | tnode(tl: Tree, tr: Tree);\n     Why = struct")]}),
    "g12-action-argument-set": ("G12", {"core": [("     c_halt: Why;\n", "     c_halt: Why;\n     c_drain: FSet(Node);\n"),
                                                 ("     + delta;\n", "     + (run == 0) -> c_drain(RQ) . Core(RQ = {})\n     + delta;\n")],
                                        "toml": [('[[action]]\nlabel = "c_halt"', '[[action]]\nlabel = "c_drain"\nkind = "output"\nargs = ["set"]\n\n[[action]]\nlabel = "c_halt"')]}),
    "g2-type-error": ("G2", {"core": [("-> c_done(x) . Core(", "-> c_done(run) . Core(")]}),
}

# Cases mcrl22lps refuses: no lps.aterm. The test runs them through run(), which
# refuses a sum variable named like a parameter (§3.5.8) before linearising.
UNLINEARISABLE = {"g2-type-error", "g4-sum-variable-shadows-parameter"}

# Cases whose LPS mcrl22lps does not make the same twice: a Core that is not linear
# gets a program counter whose state numbers and dummy globals vary from run to run
# (seen on 2026-10-07; every linear Core came out
# byte-identical in repeated runs). The fixture test compares their verdicts instead.
UNSTABLE = {"g4-program-counter"}

def _edit(text: str, edits: list) -> str:
    for old, new in edits:
        if old not in text:
            raise SystemExit("fixture edit does not apply: %r" % old[:60])
        text = text.replace(old, new)
    return text


def case_files(name: str) -> dict:
    """The files of a case: core, init, toml texts and the catalogue entries."""
    _, edits = CASES[name]
    base = {k: (HERE / "mini" / f).read_text() for k, f in (("core", "core.mcrl2"), ("init", "codegen-init.mcrl2"), ("toml", "codegen.toml"))}
    out = {k: _edit(v, edits.get(k, [])) for k, v in base.items()}
    out["catalogue"] = edits.get("catalogue", {})
    return out


def linearisation_input(core: str, init: str, toml: str) -> str:
    """What the generator linearises (docs/spec.md §3.1.1, §3.6.2)."""
    from naima_codegen.config import read_config
    from naima_codegen.frontend import linearisation_input as li
    from naima_codegen.source import read_core

    return li(core if core.endswith("\n") else core + "\n", init, read_core(core), read_config(toml)[0])


def linearise(core: str, init: str, toml: str) -> str | None:
    """The ATerm text of the LPS, by the pinned commands; None when mcrl22lps refuses."""
    from naima_codegen.toolchain import LINEARISE, PRETTY

    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        (p / "m.mcrl2").write_text(linearisation_input(core, init, toml))
        r = subprocess.run(LINEARISE + ["m.mcrl2", "m.lps"], cwd=d, capture_output=True)
        if r.returncode != 0:
            return None
        subprocess.run(PRETTY + ["m.lps", "m.aterm"], cwd=d, check=True, capture_output=True)
        return (p / "m.aterm").read_text()


def example_lps() -> str | None:
    return linearise(*((EXAMPLE / f).read_text() for f in EXAMPLE_FILES))


def build(out: Path) -> None:
    """Write every generated fixture file under out (a copy of this directory's layout)."""
    (out / "mini").mkdir(parents=True, exist_ok=True)
    mini = HERE / "mini"
    mc, mi, mt = ((mini / f).read_text() for f in ("core.mcrl2", "codegen-init.mcrl2", "codegen.toml"))
    (out / "mini" / "lps.aterm").write_text(linearise(mc, mi, mt))
    for name, (codes, _) in sorted(CASES.items()):
        d = out / "cases" / name
        d.mkdir(parents=True, exist_ok=True)
        f = case_files(name)
        (d / "core.mcrl2").write_text(f["core"])
        (d / "codegen-init.mcrl2").write_text(f["init"])
        (d / "codegen.toml").write_text(f["toml"])
        (d / "expect").write_text(codes + "\n")
        for entry, text in sorted(f["catalogue"].items()):
            (d / "catalogue" / entry).mkdir(parents=True, exist_ok=True)
            (d / "catalogue" / entry / "reference.mcrl2").write_text(text)
        own = linearisation_input(f["core"], f["init"], f["toml"]) != linearisation_input(mc, mi, mt)
        if own and name not in UNLINEARISABLE:
            a = linearise(f["core"], f["init"], f["toml"])
            if a is None:
                raise SystemExit("case %s: mcrl22lps refused it" % name)
            (d / "lps.aterm").write_text(a)


def snapshots() -> None:
    from naima_codegen.errors import GeneratorError
    from naima_codegen.frontend import check
    from naima_codegen.lps import read_lps
    from naima_codegen.render import render_lps, render_refusals

    for d, src, toml in ((HERE / "mini", HERE / "mini" / "core.mcrl2", HERE / "mini" / "codegen.toml"),
                         (HERE / "jobqueue", EXAMPLE / EXAMPLE_FILES[0], EXAMPLE / EXAMPLE_FILES[2])):
        aterm = (d / "lps.aterm").read_text()
        err = None
        try:
            check(src.read_text(), toml.read_text(), aterm, [])
        except GeneratorError as e:
            err = e
        (d / "expected.txt").write_text(render_lps(read_lps(aterm)) + render_refusals(err))


def main(argv: list) -> int:
    with tempfile.TemporaryDirectory() as t:
        out = Path(t)
        build(out)
        for p in sorted(out.rglob("*")):
            if p.is_file():
                dst = HERE / p.relative_to(out)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, dst)
    ex = HERE / "jobqueue"
    ex.mkdir(exist_ok=True)
    a = example_lps()
    if a is None:
        raise SystemExit("jobqueue: mcrl22lps refused it")
    (ex / "lps.aterm").write_text(a)
    if "--snapshots" in argv:
        snapshots()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
