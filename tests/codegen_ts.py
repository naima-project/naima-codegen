"""Running the generated TypeScript in the tests (docs/spec.md §3.18.5): Deno,
the drivers under tests/ts/, and the conversion of values between the Python
backend's form (codegen_values) and the TypeScript backend's JSON form.

TypeScript values: Bool, integers and ids as themselves, an enumeration the
name of its constructor, a record the list of its fields, a union the list
(constructor index, fields...), a list a list, a finite set a list in the
model's order, a stored function {"$col": [default, [[key, value]...]]}.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRIVERS = ROOT / "tests" / "ts"
# Pinned: the Svelte whose rune declarations the Svelte module is checked against and whose compiler builds
# it, and the TypeScript whose transpiler strips its types first (docs/spec.md §3.18.5).
SVELTE = "npm:svelte@5.57.2"


def deno() -> str | None:
    return shutil.which("deno")


def run(script: str, *args, timeout: int = 900) -> str:
    """A driver's standard output; its failure is an AssertionError with its standard error."""
    r = subprocess.run([deno(), "run", "-A", "--no-prompt", str(DRIVERS / script)] + [str(a) for a in args],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise AssertionError("%s: %s" % (script, r.stderr[-4000:]))
    return r.stdout


def run_json(script: str, data, *args) -> str:
    with tempfile.TemporaryDirectory(prefix="naima-ts-") as d:
        p = Path(d) / "in.json"
        p.write_text(json.dumps(data))
        return run(script, *args, p)


def check(*modules: Path) -> subprocess.CompletedProcess:
    """deno check of the modules, with Svelte's rune declarations in scope."""
    with tempfile.TemporaryDirectory(prefix="naima-ts-") as d:
        entry = Path(d) / "check.ts"
        entry.write_text('/// <reference types="%s" />\n' % SVELTE
                         + "".join('export * as m%d from "%s";\n' % (k, m.as_uri()) for k, m in enumerate(modules)))
        return subprocess.run([deno(), "check", "--quiet", str(entry)], capture_output=True, text=True, timeout=900)


def py2ts(v, s, types):
    """The TypeScript form of the Python value v of sort s."""
    s = types.resolve(s)
    k = types.kind(s)
    if k in ("bool", "int", "id"):
        return v
    if k == "List":
        return [py2ts(x, s[1], types) for x in v]
    if k == "FSet":
        return [py2ts(x, s[1], types) for x in sorted(v)]
    if k == "fun":
        dom = s[1]
        key = (lambda x: py2ts(x, dom[0], types)) if len(dom) == 1 else (
            lambda x: [py2ts(y, d, types) for y, d in zip(x, dom)])
        return {"$col": [py2ts(v.d, s[2], types), [[key(a), py2ts(b, s[2], types)] for a, b in sorted(v.m.items())]]}
    cons = types.lps.structs[s[1]].constructors
    if k == "enumeration":
        return cons[v][0]
    if k == "record":
        return [py2ts(x, fs, types) for x, (_, fs) in zip(v, cons[0][1])]
    return [v[0]] + [py2ts(x, fs, types) for x, (_, fs) in zip(v[1:], cons[v[0]][1])]


def ts2py(v, s, types):
    """The Python form of the TypeScript value v of sort s."""
    s = types.resolve(s)
    k = types.kind(s)
    if k in ("bool", "int", "id"):
        return v
    if k == "List":
        return tuple(ts2py(x, s[1], types) for x in v)
    if k == "FSet":
        return frozenset(ts2py(x, s[1], types) for x in v)
    cons = types.lps.structs[s[1]].constructors
    if k == "enumeration":
        return [c for c, _, _ in cons].index(v)
    if k == "record":
        return tuple(ts2py(x, fs, types) for x, (_, fs) in zip(v, cons[0][1]))
    return (v[0],) + tuple(ts2py(x, fs, types) for x, (_, fs) in zip(v[1:], cons[v[0]][1]))


def ts_entry(side: int, item: tuple, inputs, outputs, types) -> dict:
    """The TypeScript entry ({"event": ...} or {"action": ...}) of a Python entry (side, (code, args...))."""
    from naima_codegen.tsback import _fields, camel

    a = (inputs if side == 0 else outputs)[item[0]]
    x = {"type": camel(a.label)}
    for f, v, s in zip(_fields(a), item[1:], a.sorts):
        x[f] = py2ts(v, s, types)
    return {"event": x} if side == 0 else {"action": x}
