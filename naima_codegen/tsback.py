"""The TypeScript backend: `<generated>/typescript/<typescript_module>/`
(docs/spec.md §3.8, §3.18).

It emits two files. `<module>.ts` is plain TypeScript, with no Svelte and no
I/O: the types of the model's sorts, the configuration, the initial state
(§3.8.1, `init` included), the user maps and walks compiled into loops
(§3.4.8), the acceptor of the unrefined Core (§3.8.9), the encoding of entries
(§3.8.10) and, when the model has a policy (§3.7), the stepper of the refined
Core as a function from a state and an event to the next state and the actions,
with its transition table exported as data (§3.18.3). `<module>.svelte.ts`,
written only with a policy, wraps the stepper in a Svelte 5 class: the state as
`$state.raw` fields behind a read-only view, one method per input label, and
the output actions as calls to a port the caller injects (§3.18.4).

Values: Bool is `boolean`; integers and ids are `number`, every arithmetic
result checked to be a safe integer (§3.18.2); an enumeration is the name of
its constructor (a string), a record the array of its fields, a union the array
(constructor index, fields...); a list is an array, a finite set an array kept
sorted in the model's order without repeats; a function is a view: `_Col` (a
stored function, default plus entries), `_Upd` (one update over a view), `_Ov`
(an overlay, §3.4.8). A state is never changed in place once a step returns it:
`step` copies it first, and a stored function is copied before a summand writes
it.

The generated code has no recursion: walks are loops, and every compilation
step here is a loop over an explicit stack.
"""

from __future__ import annotations

import json
import re

from .expr import App, Bind, Num, Op, Var, Whr, show
from .frontend import INIT_PREFIX
from .functions import is_fun, mentions
from .header import block as _header_block
from .lower import Types, fun_params, g10, plan_summand, width_range

HEADER = _header_block("//")

RUNTIME = r'''
// deno-lint-ignore-file no-explicit-any no-unused-vars prefer-const
// deno-coverage-ignore-file

/** A value of the model inside the generated code; the exported types say what each one is. */
type V = any;

/** A stored function, read at any point of its domain (docs/spec.md §3.18.2). */
export interface Fn<K, X> {
  get(k: K): X;
}

/** An error of the generated code (docs/spec.md §3.14.2): it ends the run. */
export class StepError extends Error {
  constructor(message: string) {
    super(message);
    this.name = new.target.name;
  }
}

export class Undefined extends StepError {}

export class Overflow extends StepError {}

export class StepLimit extends StepError {}

export class NotQuiescent extends StepError {}

/** The acceptor's refusal of an entry (docs/spec.md §3.8.9). */
export class Reject extends Error {
  readonly entry: unknown;
  constructor(entry: unknown) {
    super(JSON.stringify(entry));
    this.name = new.target.name;
    this.entry = entry;
  }
}

export class Rejected extends Reject {}

export class Ambiguous extends Reject {}

export class DecodeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DecodeError";
  }
}

function _int(v: number): number {
  // Every integer the code computes is exact: a double holds it only up to 2^53 - 1 (docs/spec.md §3.18.2).
  if (!Number.isSafeInteger(v)) throw new Overflow(String(v) + " is not an exact integer");
  return v;
}

function _add(a: number, b: number): number {
  return _int(a + b);
}

function _sub(a: number, b: number): number {
  return _int(a - b);
}

function _mul(a: number, b: number): number {
  return _int(a * b);
}

function _div(a: number, b: number): number {
  if (b === 0) throw new Undefined("division by 0");
  return _int(Math.floor(a / b));
}

function _mod(a: number, b: number): number {
  if (b === 0) throw new Undefined("modulo 0");
  return _int(((a % b) + b) % b);
}

function _fit(v: number, lo: number, hi: number, name: string): number {
  // A value written to a parameter fits its width (docs/spec.md §3.12.4).
  if (v < lo || v > hi) throw new Overflow(name + " = " + v + " does not fit [" + lo + ", " + hi + "]");
  return v;
}

function _int2nat(x: number): number {
  if (x < 0) throw new Undefined("Int2Nat(" + x + ")");
  return x;
}

function _nat2pos(x: number): number {
  if (x <= 0) throw new Undefined("Nat2Pos(" + x + ")");
  return x;
}

function _cmp1(a: V, b: V): number {
  if (typeof a === "string") return _RANK[a] - _RANK[b];
  if (typeof a === "boolean") return (a ? 1 : 0) - (b ? 1 : 0);
  return a < b ? -1 : a > b ? 1 : 0;
}

/** The model's order on values: numbers, false before true, constructors in declaration order, arrays lexicographically. */
function _cmp(a: V, b: V): number {
  const st: [V[], V[], number][] = [];
  let x = a;
  let y = b;
  for (;;) {
    if (Array.isArray(x)) {
      st.push([x, y, 0]);
    } else {
      const c = _cmp1(x, y);
      if (c !== 0) return c < 0 ? -1 : 1;
    }
    for (;;) {
      const top = st[st.length - 1];
      if (top === undefined) return 0;
      const [p, q, i] = top;
      if (i >= p.length || i >= q.length) {
        st.pop();
        if (p.length !== q.length) return p.length < q.length ? -1 : 1;
        continue;
      }
      top[2] = i + 1;
      x = p[i];
      y = q[i];
      break;
    }
  }
}

function _eq(a: V, b: V): boolean {
  return a === b || (Array.isArray(a) && _cmp(a, b) === 0);
}

function _key(k: V): string {
  return typeof k === "number" ? String(k) : JSON.stringify(k);
}

class _Col {
  // A stored function: a default and the entries that differ from it.
  d: V;
  m: Map<string, [V, V]>;
  r: [number, number, string] | null;

  constructor(d: V) {
    this.d = d;
    this.m = new Map();
    this.r = null; // the width its values fit (docs/spec.md §3.12.4)
  }

  get(k: V): V {
    const e = this.m.get(_key(k));
    return e === undefined ? this.d : e[1];
  }

  set(k: V, v: V): void {
    if (this.r !== null) _fit(v, this.r[0], this.r[1], this.r[2]);
    if (_eq(v, this.d)) this.m.delete(_key(k));
    else this.m.set(_key(k), [k, v]);
  }

  clone(): _Col {
    const c = new _Col(this.d);
    c.m = new Map(this.m);
    c.r = this.r;
    return c;
  }

  entries(): [V, V][] {
    return [...this.m.values()].sort((p, q) => _cmp(p[0], q[0]));
  }
}

class _Upd {
  // f[k -> v] over a view, read only.
  constructor(readonly b: V, readonly k: V, readonly v: V) {}

  get(x: V): V {
    return _eq(x, this.k) ? this.v : this.b.get(x);
  }
}

class _Ov {
  // An overlay (docs/spec.md §3.4.8): the entries written during a call over an unchanged view.
  m: Map<string, V>;

  constructor(readonly b: V) {
    this.m = new Map();
  }

  get(x: V): V {
    const k = _key(x);
    return this.m.has(k) ? this.m.get(k) : this.b.get(x);
  }

  set(k: V, v: V): void {
    this.m.set(_key(k), v);
  }
}

function _run(view: V, plan: V[]): V {
  for (const op of plan) {
    if (op[0] === 0) view.set(op[1], op[2]);
    else op[1](view);
  }
  return view;
}

function _head(l: V[]): V {
  if (l.length === 0) throw new Undefined("head of []");
  return l[0];
}

function _tail(l: V[]): V[] {
  if (l.length === 0) throw new Undefined("tail of []");
  return l.slice(1);
}

function _rhead(l: V[]): V {
  if (l.length === 0) throw new Undefined("rhead of []");
  return l[l.length - 1];
}

function _rtail(l: V[]): V[] {
  if (l.length === 0) throw new Undefined("rtail of []");
  return l.slice(0, -1);
}

function _at(l: V[], i: number): V {
  if (i >= l.length) throw new Undefined("element " + i + " of a list of " + l.length);
  return l[i];
}

function _has(s: V[], x: V): boolean {
  return s.some((y) => _eq(x, y));
}

function _ins(s: V[], x: V): V[] {
  return _has(s, x) ? s : [...s, x].sort(_cmp);
}

function _union(a: V[], b: V[]): V[] {
  return [...a, ...b.filter((x) => !_has(a, x))].sort(_cmp);
}

function _diff(a: V[], b: V[]): V[] {
  return a.filter((x) => !_has(b, x));
}

function _inter(a: V[], b: V[]): V[] {
  return a.filter((x) => _has(b, x));
}

function _subset(a: V[], b: V[]): boolean {
  return a.every((x) => _has(b, x));
}

function _least(w: V[]): V {
  if (w.length === 0) throw new Undefined("least of an empty set");
  return w[0];
}

class _Index {
  // An index of the policy (docs/spec.md §3.7.6, §3.18.2): each member's class and order key.
  // Order keys: the id (least), the key's value (key), minus the entry stamp (newest), the stamp (oldest).
  ent: Map<number, [number, V]>;
  stamp: number;

  constructor(readonly order: number) {
    this.ent = new Map();
    this.stamp = 0;
  }

  clone(): _Index {
    const c = new _Index(this.order);
    c.ent = new Map(this.ent);
    c.stamp = this.stamp;
    return c;
  }

  refresh(v: number, m: boolean, c: number, k: V): void {
    const old = this.ent.get(v);
    if (!m) {
      this.ent.delete(v);
      return;
    }
    let ok: V;
    if (this.order === 0) ok = v;
    else if (this.order === 1) ok = k;
    else if (old !== undefined) ok = old[1]; // a member keeps its stamp, across a change of class too
    else {
      this.stamp += 1;
      ok = this.order === 2 ? -this.stamp : this.stamp;
    }
    this.ent.set(v, [c, ok]);
  }

  #best(adm: ((c: number) => boolean) | null): number | null {
    let best: [V, number] | null = null;
    for (const [v, [c, ok]] of this.ent) {
      if (adm !== null && !adm(c)) continue;
      if (best === null || _cmp([ok, v], best) < 0) best = [ok, v];
    }
    return best === null ? null : best[1];
  }

  first(adm: ((c: number) => boolean) | null): number {
    const v = this.#best(adm);
    if (v === null) throw new Undefined("first of an index without an admitted member");
    return v;
  }

  some(adm: ((c: number) => boolean) | null): boolean {
    return this.#best(adm) !== null;
  }

  /** The members in index order. */
  ordered(): number[] {
    return [...this.ent.entries()].sort((p, q) => _cmp([p[1][1], p[0]], [q[1][1], q[0]])).map((e) => e[0]);
  }
}

function _enc(v: V, d: V, out: number[]): void {
  const stack: [V, V][] = [[v, d]];
  for (;;) {
    const top = stack.pop();
    if (top === undefined) return;
    let [x, e] = top;
    const k = e[0];
    if (k === "b") {
      out.push(x ? 1 : 0);
    } else if (k === "n" || k === "i" || k === "e") {
      if (k === "e") x = e[1].indexOf(x);
      if (k === "i") x = x >= 0 ? x * 2 : -x * 2 - 1;
      if (x < 0) throw new StepError("a negative Nat in an entry");
      for (;;) {
        const b = x % 128;
        x = Math.floor(x / 128);
        if (x) out.push(b + 128);
        else {
          out.push(b);
          break;
        }
      }
    } else if (k === "l") {
      for (let j = x.length - 1; j >= 0; j--) stack.push([x[j], e[1]]);
      stack.push([x.length, ["n"]]);
    } else if (k === "r") {
      for (let j = x.length - 1; j >= 0; j--) stack.push([x[j], e[1][j]]);
    } else {
      const f = e[1][x[0]];
      for (let j = x.length - 1; j >= 1; j--) stack.push([x[j], f[j - 1]]);
      stack.push([x[0], ["n"]]);
    }
  }
}

function _dec(data: Uint8Array, pos: number, d: V): [V, number] {
  // (value, next position): frames on an explicit stack, values on another.
  const vals: V[] = [];
  const stack: V[] = [["v", d]];
  for (;;) {
    const op = stack.pop();
    if (op === undefined) break;
    if (op[0] === "v") {
      const k = op[1][0];
      if (k === "b") {
        if (pos >= data.length || data[pos] > 1) throw new DecodeError("a Bool");
        vals.push(data[pos] === 1);
        pos += 1;
      } else if (k === "n" || k === "i" || k === "e") {
        let x = 0;
        let sh = 1;
        for (;;) {
          if (pos >= data.length || sh > 2 ** 56) throw new DecodeError("a number");
          const b = data[pos];
          pos += 1;
          x += (b % 128) * sh;
          sh *= 128;
          if (b < 128) break;
        }
        if (k === "i") x = x % 2 ? -(x + 1) / 2 : x / 2;
        if (k === "e") {
          if (x >= op[1][1].length) throw new DecodeError("a constructor index");
          x = op[1][1][x];
        }
        vals.push(x);
      } else if (k === "l") {
        stack.push(["list", op[1][1]]);
        stack.push(["v", ["n"]]);
      } else if (k === "r") {
        stack.push(["tuple", op[1][1].length, null]);
        for (let j = op[1][1].length - 1; j >= 0; j--) stack.push(["v", op[1][1][j]]);
      } else {
        stack.push(["union", op[1]]);
        stack.push(["v", ["n"]]);
      }
    } else if (op[0] === "list") {
      const n = vals.pop();
      stack.push(["tuple", n, null]);
      for (let j = 0; j < n; j++) stack.push(["v", op[1]]);
    } else if (op[0] === "union") {
      const c = vals.pop();
      if (c >= op[1][1].length) throw new DecodeError("a constructor index");
      const f = op[1][1][c];
      stack.push(["tuple", f.length, c]);
      for (let j = f.length - 1; j >= 0; j--) stack.push(["v", f[j]]);
    } else {
      const n = op[1];
      const items = vals.splice(vals.length - n, n);
      vals.push(op[2] === null ? items : [op[2], ...items]);
    }
  }
  return [vals[0], pos];
}
'''

_KEYWORDS = {
    "break", "case", "catch", "class", "const", "continue", "debugger", "default", "delete", "do", "else", "enum",
    "export", "extends", "false", "finally", "for", "function", "if", "import", "in", "instanceof", "new", "null",
    "return", "super", "switch", "this", "throw", "true", "try", "typeof", "var", "void", "while", "with", "yield",
    "let", "static", "implements", "interface", "package", "private", "protected", "public", "await", "arguments",
    "eval", "undefined", "NaN", "Infinity", "as", "async", "of", "type", "constructor", "prototype", "__proto__",
}
# Names the generated module exports itself.
_TAKEN = {"Fn", "StepError", "Undefined", "Overflow", "StepLimit", "NotQuiescent", "Reject", "Rejected", "Ambiguous",
          "DecodeError", "Config", "State", "Event", "Action", "Entry", "Step", "Acceptor", "Arg", "Sort",
          "InputTransition", "OutputTransition", "V", "Map", "Set", "Array", "Number", "String", "Boolean", "Object",
          "Error", "JSON", "Math", "Uint8Array", "Record", "Readonly", "Promise"}
# Members of the generated Svelte class, which an input method must not take.
_MEMBERS = {"view", "allowed", "stopped", "constructor"}


def ident(name: str) -> str:
    """A TypeScript identifier for a model name (§3.3.8: a reserved word gets a trailing underscore)."""
    n = re.sub(r"[^A-Za-z0-9_]", lambda m: "_q" if m.group(0) == "'" else "_", name)
    return n + "_" if n in _KEYWORDS else n


def type_name(sort_name: str) -> str:
    n = ident(sort_name)
    return n + "_" if n in _TAKEN else n


def camel(label: str) -> str:
    """The vocabulary name of a label in TypeScript: c_give_up -> giveUp."""
    parts = [p for p in (label[2:] if label.startswith("c_") else label).split("_") if p]
    n = parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:]) if parts else "x"
    return ident(n)


def class_name(module: str) -> str:
    parts = [p for p in re.split(r"[^A-Za-z0-9]", module) if p]
    return "".join(p[:1].upper() + p[1:] for p in parts) or "Machine"


def _q(s: str) -> str:
    return json.dumps(s)


class Emitter:
    """Compiles expressions of the accepted model into TypeScript."""

    def __init__(self, model):
        self.model = model
        self.lps = model.lps
        self.types = Types(model.lps, model.config.id_sorts)
        self.maps = {m.name: m for m in model.lps.maps}
        self.fun_maps = model.fun_maps
        self.walks = model.walks
        self.tmp = 0

    def fresh(self, base: str = "t") -> str:
        self.tmp += 1
        return "_%s%d" % (base, self.tmp)

    # ------------------------------------------------------------ types

    def ts_type(self, s) -> str:
        """The exported TypeScript type of a value of sort s."""
        out: list = []
        stack = [(s, False)]
        while stack:
            x, seen = stack.pop()
            x = self.types.resolve(x)
            k = self.types.kind(x)
            if k == "bool":
                out.append("boolean")
            elif k == "int":
                out.append("number")
            elif k in ("id", "enumeration", "record", "union"):
                out.append(type_name(x[1]))
            elif k in ("List", "FSet"):
                if not seen:
                    stack.append((x, True))
                    stack.append((x[1], False))
                else:
                    out.append("readonly %s[]" % _paren(out.pop()))
            elif k == "fun":
                if not seen:
                    stack.append((x, True))
                    stack.append((x[2], False))
                    for d in reversed(x[1]):
                        stack.append((d, False))
                else:
                    cod = out.pop()
                    n = len(x[1])
                    ds = out[len(out) - n:]
                    del out[len(out) - n:]
                    out.append("Fn<%s, %s>" % (ds[0] if n == 1 else "readonly [%s]" % ", ".join(ds), cod))
            else:
                raise g10("sort %s" % (x,), "no TypeScript type for it")
        return out[0]

    def sort_desc(self, s) -> str:
        """The description of a sort the transition table carries, for enumerating arguments (§3.18.3)."""
        s = self.types.resolve(s)
        k = self.types.kind(s)
        if k == "bool":
            return '{ kind: "bool" }'
        if k == "int":
            return '{ kind: "int", sort: %s }' % _q(s[1])
        if k == "id":
            return '{ kind: "id", sort: %s, ctor: %s }' % (_q(s[1]), _q(self.lps.structs[s[1]].constructors[0][0]))
        if k == "enumeration":
            return '{ kind: "enum", sort: %s, values: %s }' % (
                _q(s[1]), json.dumps([c for c, _, _ in self.lps.structs[s[1]].constructors]))
        return '{ kind: "other", sort: %s }' % _q(show_sort(s))

    def codec_desc(self, sort):
        """As lower.codec_desc, with ("e", names) for an enumeration, whose values are names here."""
        types = self.types
        out: list = []
        stack = [(sort, False)]
        while stack:
            s, seen = stack.pop()
            s = types.resolve(s)
            k = types.kind(s)
            if k == "bool":
                out.append(["b"])
            elif k == "int":
                out.append(["i"] if s[1] == "Int" else ["n"])
            elif k == "id":
                out.append(["n"])
            elif k == "enumeration":
                out.append(["e", [c for c, _, _ in types.lps.structs[s[1]].constructors]])
            elif k == "List":
                if not seen:
                    stack.append((s, True))
                    stack.append((s[1], False))
                else:
                    out.append(["l", out.pop()])
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
                    out.append(["r", vals])
                else:
                    per, i = [], 0
                    for _, f, _ in cons:
                        per.append(vals[i:i + len(f)])
                        i += len(f)
                    out.append(["u", per])
            else:
                raise g10("sort %s" % (s,), "no entry encoding for it (§3.5.7)")
        return out[0]

    def label_fn(self, s, v: str) -> str:
        """TypeScript that prints the value v of sort s as mCRL2 prints it (ids in the model's form, job(3))."""
        s = self.types.resolve(s)
        k = self.types.kind(s)
        if k in ("bool", "int", "enumeration"):
            return "String(%s)" % v
        if k == "id":
            return '"%s(" + %s + ")"' % (self.lps.structs[s[1]].constructors[0][0], v)
        return "_show(%s, %s)" % (v, json.dumps(self._show_desc(s)))

    def _show_desc(self, sort):
        """The shape `_show` follows: ["v"] printed as is, ["id", ctor], ["l", elem], ["r", ctor, fields], ["u", [[ctor, fields]...]]."""
        types = self.types
        out: list = []
        stack = [(sort, False)]
        while stack:
            s, seen = stack.pop()
            s = types.resolve(s)
            k = types.kind(s)
            if k in ("bool", "int", "enumeration"):
                out.append(["v"])
            elif k == "id":
                out.append(["id", types.lps.structs[s[1]].constructors[0][0]])
            elif k in ("List", "FSet"):
                if not seen:
                    stack.append((s, True))
                    stack.append((s[1], False))
                else:
                    out.append(["l" if k == "List" else "s", out.pop()])
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
                per, i = [], 0
                for c, f, _ in cons:
                    per.append([c, vals[i:i + len(f)]])
                    i += len(f)
                out.append(["r", per[0][0], per[0][1]] if k == "record" else ["u", per])
            else:
                raise g10("sort %s" % (s,), "no mCRL2 text for it")
        return out[0]

    # ------------------------------------------------------------ expressions

    def expr(self, e, scope: dict) -> str:
        """TypeScript for e; function-sorted subexpressions become views."""
        out: list = []
        stack = [(e, False)]
        while stack:
            x, seen = stack.pop()
            if isinstance(x, Num):
                out.append(str(x.value))
                continue
            if isinstance(x, Var):
                if x.name not in scope:
                    raise g10("expression %s" % show(e)[:80], "variable %s is not in scope" % x.name)
                out.append(scope[x.name])
                continue
            if isinstance(x, Op):
                out.append(self._const(x))
                continue
            if isinstance(x, Bind):
                out.append(self._bind(x, scope))
                continue
            if isinstance(x, Whr):
                names = {}
                vals = []
                for v, d in x.defs:
                    vals.append(self.expr(d, scope))
                    names[v.name] = ident("w_" + v.name) + self.fresh("")
                inner = dict(scope)
                inner.update(names)
                body = self.expr(x.body, inner)
                out.append("((%s) => %s)(%s)" % (", ".join("%s: V" % names[v.name] for v, _ in x.defs), body,
                                                 ", ".join(vals)))
                continue
            h = x.head
            hn = h.name if isinstance(h, Op) else None
            if hn == "if" and len(x.args) == 3:
                if not seen:
                    stack.append((x, True))
                    for a in reversed(x.args):
                        stack.append((a, False))
                    continue
                c, a, b = out.pop(-3), out.pop(-2), out.pop()
                out.append("(%s ? %s : %s)" % (c, a, b))
                continue
            if hn is not None and hn.startswith("ix_"):
                out.append(self.ix_app(x, hn, scope))
                continue
            if hn in self.fun_maps or (hn in self.walks):
                out.append(self.call(x, scope))
                continue
            if not seen:
                stack.append((x, True))
                for a in reversed(x.args):
                    stack.append((a, False))
                if not isinstance(h, Op):
                    stack.append((h, False))
                continue
            n = len(x.args)
            args = out[len(out) - n:]
            del out[len(out) - n:]
            if not isinstance(h, Op):
                f = out.pop()
                out.append("%s.get(%s)" % (f, args[0]) if n == 1 else "%s.get([%s])" % (f, ", ".join(args)))
                continue
            out.append(self._app(x, hn, args))
        return out[-1]

    def ix_app(self, x, hn: str, scope: dict) -> str:
        """A selection operation of the refined Core (docs/spec.md §3.7.5): the
        index implementations in place of the reference definitions."""
        if hn.startswith("ix_least_"):
            return "_least(%s)" % self.expr(x.args[0], scope)
        for op in ("first", "some"):
            if hn.startswith("ix_%s_" % op):
                return "_ix%s_%s(s)" % (op, hn[len("ix_%s_" % op):])
        raise g10("operator %s" % hn, "a generator map where only first, some and least may appear")

    def _const(self, x: Op) -> str:
        n = x.name
        if n in ("true", "false"):
            return n
        if n in ("[]", "{}", "@fset_empty"):
            return "[]"
        if n in self.types.cons:
            c = self.types.cons[n]
            if c.shape == "enumeration":
                return _q(c.name)
            if c.shape == "union":
                return "[%d]" % c.index
            return "[]"
        if n in self.maps:
            return "_m_%s()" % ident(n)
        raise g10("operator %s" % n, "no TypeScript for this constant")

    def _bind(self, x: Bind, scope: dict) -> str:
        if x.kind == "Lambda":
            if len(x.vars) != 1:
                raise g10("lambda", "a lambda over %d variables" % len(x.vars))
            return "new _Col(%s)" % self.expr(x.body, scope)
        if x.kind in ("Exists", "Forall") and len(x.vars) == 1:
            v = x.vars[0].name
            mem, rest = x.body.args[0], x.body.args[1]
            col = mem.args[1]
            name = ident("q_" + v) + self.fresh("")
            inner = dict(scope)
            inner[v] = name
            c = self.expr(col, scope)
            r = self.expr(rest, inner)
            return "%s.%s((%s: V) => %s)" % (c, "some" if x.kind == "Exists" else "every", name, r)
        raise g10("binder %s" % x.kind, "no TypeScript for it here")

    def _app(self, x, hn, a) -> str:
        t = self.types
        sorts = x.head.sort
        dom = sorts[1] if sorts[0] == "->" else ()
        k0 = t.kind(dom[0]) if dom else None
        scalar = k0 in ("bool", "int", "id", "enumeration")
        if hn in t.cons:
            c = t.cons[hn]
            if c.shape == "id":
                return a[0]
            if c.shape == "record":
                return "[%s]" % ", ".join(a)
            return "[%d, %s]" % (c.index, ", ".join(a))
        if hn in t.proj:
            cands = t.proj[hn]
            c, j = cands[0]
            if c.shape == "id":
                return a[0]
            if len(cands) > 1 and len({(cc.shape, jj) for cc, jj in cands}) > 1:
                raise g10("projection %s" % hn, "a projection shared by constructors at different positions")
            return "%s[%d]" % (a[0], j if c.shape == "record" else j + 1)
        if hn in t.recog:
            return "(%s[0] === %d)" % (a[0], t.recog[hn].index)
        if hn in self.maps and hn not in ("if",):
            return "_m_%s(%s)" % (ident(hn), ", ".join(a))
        if hn in ("&&", "||") and len(a) == 2:
            return "(%s %s %s)" % (a[0], hn, a[1])
        if hn in ("==", "!=") and len(a) == 2:
            if scalar:
                return "(%s %s %s)" % (a[0], "===" if hn == "==" else "!==", a[1])
            return "%s_eq(%s, %s)" % ("" if hn == "==" else "!", a[0], a[1])
        if hn in ("<", "<=", ">", ">=") and len(a) == 2:
            if k0 == "int":
                return "(%s %s %s)" % (a[0], hn, a[1])
            if k0 == "FSet":
                lo, hi = (a[0], a[1]) if hn in ("<", "<=") else (a[1], a[0])
                strict = " && %s.length < %s.length" % (lo, hi) if hn in ("<", ">") else ""
                return "(_subset(%s, %s)%s)" % (lo, hi, strict)
            return "(_cmp(%s, %s) %s 0)" % (a[0], a[1], hn)
        if hn == "=>":
            return "(!%s || %s)" % (a[0], a[1])
        if hn == "!":
            return "(!%s)" % a[0]
        if hn == "in":
            return "_has(%s, %s)" % (a[1], a[0])
        if hn == "+" and len(a) == 2:
            return "_union(%s, %s)" % (a[0], a[1]) if k0 == "FSet" else "_add(%s, %s)" % (a[0], a[1])
        if hn == "-" and len(a) == 2:
            return "_diff(%s, %s)" % (a[0], a[1]) if k0 == "FSet" else "_sub(%s, %s)" % (a[0], a[1])
        if hn == "-" and len(a) == 1:
            return "_sub(0, %s)" % a[0]
        if hn == "*" and len(a) == 2:
            return "_inter(%s, %s)" % (a[0], a[1]) if k0 == "FSet" else "_mul(%s, %s)" % (a[0], a[1])
        if hn == "div":
            return "_div(%s, %s)" % (a[0], a[1])
        if hn == "mod":
            return "_mod(%s, %s)" % (a[0], a[1])
        if hn in ("min", "max"):
            return "Math.%s(%s, %s)" % (hn, a[0], a[1])
        if hn == "succ":
            return "_add(%s, 1)" % a[0]
        if hn == "pred":
            return "_sub(%s, 1)" % a[0]
        if hn == "abs":
            return "Math.abs(%s)" % a[0]
        if hn in ("Pos2Nat", "Nat2Int", "Pos2Int", "@cNat", "@cInt"):
            return a[0]
        if hn == "@cNeg":
            return "_sub(0, %s)" % a[0]
        if hn == "Int2Nat":
            return "_int2nat(%s)" % a[0]
        if hn == "Nat2Pos":
            return "_nat2pos(%s)" % a[0]
        if hn == "|>":
            return "[%s, ...%s]" % (a[0], a[1])
        if hn == "<|":
            return "[...%s, %s]" % (a[0], a[1])
        if hn == "++":
            return "[...%s, ...%s]" % (a[0], a[1])
        if hn == "head":
            return "_head(%s)" % a[0]
        if hn == "tail":
            return "_tail(%s)" % a[0]
        if hn == "rhead":
            return "_rhead(%s)" % a[0]
        if hn == "rtail":
            return "_rtail(%s)" % a[0]
        if hn == "#":
            return "%s.length" % a[0]
        if hn == ".":
            return "_at(%s, %s)" % (a[0], a[1])
        if hn in ("@fset_cons", "@fset_insert"):
            return "_ins(%s, %s)" % (a[1], a[0])
        if hn == "@func_update":
            return "new _Upd(%s, %s, %s)" % (a[0], a[1], a[2])
        raise g10("operator %s" % hn, "no TypeScript for it")

    # ------------------------------------------------------------ calls and plans

    def written(self, name: str) -> set:
        """The function-sorted argument positions a map writes (§3.4.8)."""
        w = self.walks.get(name)
        out = set(w.overlays) if w else set()
        r = self.fun_maps.get(name, ((), None))[1]
        if r is not None:
            out.add(r)
        return out

    def call(self, x, scope: dict, target: str | None = None, eager: bool = False):
        """A call of a user map; `target` is the stored function updated in place
        at the result position (§3.4.8), otherwise every written position gets an
        overlay. With `eager`, the arguments that are not functions are evaluated
        when the plan is made, before anything is written (§3.8.4): (call, [(name, value)])."""
        name = x.head.name
        k = self.fun_maps.get(name, ((), None))[1]
        fpos = self.fun_maps.get(name, ((), None))[0]
        args, bound = [], []
        for j, a in enumerate(x.args):
            if j == k and target is not None:
                args.append(target)
                continue
            s = self.expr(a, scope)
            if j in self.written(name):
                s = "new _Ov(%s)" % s
            elif eager and j not in fpos:
                d = "_a%d" % j
                bound.append((d, s))
                s = d
            args.append(s)
        text = "_m_%s(%s)" % (ident(name), ", ".join(args))
        return (text, bound) if eager else text

    def plan(self, fe, root: str, scope: dict, lines: list, indent: str, planvar: str):
        """Lines that push onto `planvar` the writes of a function expression built
        from `root` (§3.4.8), evaluating every index, value and condition now,
        before anything is written (§3.8.4)."""
        stack = [("fe", fe, indent)]
        while stack:
            kind, x, ind = stack.pop()
            if kind == "line":
                lines.append(ind + x)
                continue
            if isinstance(x, Var):
                continue
            h = x.head.name if isinstance(x, App) and isinstance(x.head, Op) else None
            if h == "@func_update":
                i = self.expr(x.args[1], scope)
                v = self.expr(x.args[2], scope)
                stack.append(("line", "%s.push([0, %s, %s]);" % (planvar, i, v), ind))
                stack.append(("fe", x.args[0], ind))
            elif h == "if":
                c = self.expr(x.args[0], scope)
                stack.append(("line", "}", ind))
                stack.append(("fe", x.args[2], ind + "  "))
                stack.append(("line", "} else {", ind))
                stack.append(("fe", x.args[1], ind + "  "))
                stack.append(("line", "if (%s) {" % c, ind))
            elif h in self.fun_maps and self.fun_maps[h][1] is not None:
                k = self.fun_maps[h][1]
                body, bound = self.call(x, scope, target="_tgt", eager=True)
                if bound:
                    fn = "((%s) => (_tgt: V) => %s)(%s)" % (", ".join("%s: V" % d for d, _ in bound), body,
                                                         ", ".join(v for _, v in bound))
                else:
                    fn = "(_tgt: V) => %s" % body
                stack.append(("line", "%s.push([1, %s]);" % (planvar, fn), ind))
                stack.append(("fe", x.args[k], ind))
            else:
                raise g10("function expression %s" % show(x)[:80], "not built from %s" % root)
        return lines

    # ------------------------------------------------------------ maps and walks

    def emit_map(self, m, eqs) -> list:
        name = m.name
        if name in self.walks:
            return self.emit_walk(m, self.walks[name])
        params = ["a%d" % j for j in range(m.arity)]
        lines = ["function _m_%s(%s): V {" % (ident(name), ", ".join("%s: V" % p for p in params))]
        ret = self.fun_maps.get(name, ((), None))[1]
        open_end = False
        for e in eqs:
            lhs = e.lhs
            if not isinstance(lhs, App):
                lines.append("  return %s;" % self.expr(e.rhs, {}))
                continue
            scope, cond = {}, []
            for j, p in enumerate(lhs.args):
                if isinstance(p, Var):
                    scope[p.name] = params[j]
                    continue
                c = self.types.cons[p.head.name if isinstance(p, App) else p.name]
                if c.shape == "enumeration":
                    cond.append("%s === %s" % (params[j], _q(c.name)))
                elif c.shape == "union":
                    cond.append("%s[0] === %d" % (params[j], c.index))
                if isinstance(p, App):
                    for k, v in enumerate(p.args):
                        scope[v.name] = params[j] if c.shape == "id" else "%s[%d]" % (
                            params[j], k if c.shape == "record" else k + 1)
            ind = "  "
            if cond:
                lines.append("  if (%s) {" % " && ".join(cond))
                ind = "    "
            if ret is not None:
                pl: list = []
                self.plan(e.rhs, lhs.args[ret].name, scope, pl, ind, "_p")
                lines.append(ind + "const _p: V[] = [];")
                lines.extend(pl)
                lines.append(ind + "return _run(%s, _p);" % params[ret])
            else:
                lines.append(ind + "return %s;" % self.expr(e.rhs, scope))
            if cond:
                lines.append("  }")
            open_end = bool(cond)
        if open_end:
            lines.append("  throw new Undefined(%s);" % _q("no equation of %s applies" % name))
        lines.append("}")
        return lines

    def emit_walk(self, m, w) -> list:
        """A walk compiled into one loop (§3.4.8)."""
        n = len(w.params)
        lines = ["function _m_%s(%s): V {" % (ident(m.name), ", ".join("a%d: V" % j for j in range(n)))]
        var = {p: "v_" + ident(p) for p in w.params}
        for j, p in enumerate(w.params):
            lines.append("  let %s: V = a%d;" % (var[p], j))
        lst = var[w.tail]
        lines += ["  let _i = 0;", "  const _n = %s.length;" % lst, "  let _r: V;"]
        if w.op == "+":
            lines.append("  let _acc = 0;")
        elif w.op == "|>":
            lines.append("  const _pre: V[] = [];")
        lines.append("  while (true) {")
        base_scope = {p: var[p] for p in w.params}
        base_scope[w.tail] = "[]"
        lines.append("    if (_i === _n) {")
        lines.extend(self._walk_exit(w, w.base, base_scope, "      "))
        lines.append("      break;")
        lines.append("    }")
        scope = {p: var[p] for p in w.params}
        scope[w.head] = "_x"
        scope[w.tail] = "%s.slice(_i + 1)" % lst
        lines.append("    const _x = %s[_i];" % lst)
        stack = [("R", w.body, "    ", scope)]
        while stack:
            kind, x, ind, sc = stack.pop()
            if kind == "line":
                lines.append(ind + x)
                continue
            if not mentions(x, m.name):
                lines.extend(self._walk_exit(w, x, sc, ind))
                lines.append(ind + "break;")
                continue
            if isinstance(x, Whr):
                sc2 = dict(sc)
                for v, d in x.defs:
                    nm = ident("w_" + v.name) + self.fresh("")
                    lines.append(ind + "const %s = %s;" % (nm, self.expr(d, sc)))
                    sc2[v.name] = nm
                stack.append(("R", x.body, ind, sc2))
                continue
            h = x.head.name
            if h == m.name:
                lines.extend(self._walk_continue(w, m, x, sc, ind))
                continue
            if h == "if":
                c = self.expr(x.args[0], sc)
                stack.append(("line", "}", ind, None))
                stack.append(("R", x.args[2], ind + "  ", sc))
                stack.append(("line", "} else {", ind, None))
                stack.append(("R", x.args[1], ind + "  ", sc))
                lines.append(ind + "if (%s) {" % c)
                continue
            left = not mentions(x.args[0], m.name)
            e, r = (x.args[0], x.args[1]) if left else (x.args[1], x.args[0])
            ev = self.expr(e, sc)
            if h == "+":
                lines.append(ind + "_acc = _add(_acc, %s);" % ev)
            elif h == "||":
                lines += [ind + "if (%s) {" % ev, ind + "  _r = true;", ind + "  break;", ind + "}"]
            elif h == "&&":
                lines += [ind + "if (!%s) {" % ev, ind + "  _r = false;", ind + "  break;", ind + "}"]
            elif h == "|>":
                lines.append(ind + "_pre.push(%s);" % ev)
            stack.append(("R", r, ind, sc))
        lines.append("  }")
        if w.op == "+":
            lines.append("  return _add(_acc, _r);")
        elif w.op == "|>":
            lines.append("  return [..._pre, ..._r];")
        else:
            lines.append("  return _r;")
        lines.append("}")
        return lines

    def _walk_exit(self, w, x, scope, ind) -> list:
        if w.ret_pos is None:
            return [ind + "_r = %s;" % self.expr(x, scope)]
        root = w.params[w.ret_pos]
        pl: list = []
        self.plan(x, root, scope, pl, ind, "_p")
        return [ind + "const _p: V[] = [];"] + pl + [ind + "_r = _run(%s, _p);" % scope[root]]

    def _walk_continue(self, w, m, x, scope, ind) -> list:
        lines, temps, plans = [], [], []
        for j, (p, a) in enumerate(zip(w.params, x.args)):
            if j == w.pos:
                continue
            if isinstance(a, Var) and a.name == p:
                continue
            if is_fun(self.lps, m.sort[1][j]):
                pv = self.fresh("p")
                lines.append(ind + "const %s: V[] = [];" % pv)
                pl: list = []
                self.plan(a, p, scope, pl, ind, pv)
                lines.extend(pl)
                plans.append((scope[p], pv))
            else:
                t = self.fresh()
                lines.append(ind + "const %s = %s;" % (t, self.expr(a, scope)))
                temps.append((scope[p], t))
        for v, pv in plans:
            lines.append(ind + "_run(%s, %s);" % (v, pv))
        for v, t in temps:
            lines.append(ind + "%s = %s;" % (v, t))
        lines += [ind + "_i += 1;", ind + "continue;"]
        return lines


def _paren(t: str) -> str:
    return "(%s)" % t if " " in t and not t.startswith("readonly [") else t


def show_sort(s) -> str:
    if s[0] == "id":
        return s[1]
    if s[0] == "->":
        return "(%s -> %s)" % (" # ".join(show_sort(d) for d in s[1]), show_sort(s[2]))
    return "%s(%s)" % (s[0], show_sort(s[1]))


# ------------------------------------------------------------------ the module

_ORDERS = {"least": 0, "key": 1, "newest": 2, "oldest": 3}


def _labels(model):
    inputs = sorted((a for a in model.actions if a.kind == "input"), key=lambda a: a.label)
    outputs = sorted((a for a in model.actions if a.kind == "output"), key=lambda a: a.label)
    for side in (inputs, outputs):
        names = [camel(a.label) for a in side]
        if len(set(names)) != len(names):
            raise g10("labels", "two labels with the same TypeScript name: %s" % ", ".join(sorted(names)))
    return inputs, outputs


def _fields(a) -> list:
    """The TypeScript field names of a label's arguments, from `args` (§3.6.3)."""
    out = []
    for j in range(len(a.sorts)):
        n = ident(a.args[j]) if j < len(a.args) else "a%d" % j
        out.append(n + "_" if n == "type" else n)
    return out


def emit(model, st=None, module: str = "generated") -> dict:
    """{file name: text} of <generated>/typescript/<module>/: <module>.ts and,
    with st (a `stepper.Stepper`), <module>.svelte.ts (docs/spec.md §3.18)."""
    out = {"%s.ts" % module: _plain(model, st, module)}
    if st is not None:
        out["%s.svelte.ts" % module] = _svelte(model, st, module)
    return out


def _plain(model, st, module: str) -> str:
    em = Emitter(st.model if st is not None else model)
    lps = model.lps
    t = em.types
    inputs, outputs = _labels(model)
    lines = [HEADER.rstrip("\n"),
             "// The model's generated code as TypeScript, without Svelte (docs/spec.md §3.18): the types, the",
             "// configuration, the acceptor, the entry codec%s." % (
                 " and the stepper with its transition table" if st is not None else ""),
             RUNTIME.rstrip("\n")]
    # The order of the model's constructors, for _cmp.
    rank = {}
    for name, s in lps.structs.items():
        if t.shape[name] == "enumeration":
            for k, (c, _, _) in enumerate(s.constructors):
                if rank.get(c, k) != k:
                    raise g10("constructor %s" % c, "two enumerations with this constructor at different positions")
                rank[c] = k
    lines += ["", "const _RANK: Record<string, number> = %s;" % json.dumps(rank, sort_keys=True)]
    # Types of the model's sorts.
    lines += ["", "// ---- the model's sorts (docs/spec.md §3.18.2)"]
    for name, s in lps.structs.items():
        shape = t.shape[name]
        tn = type_name(name)
        if shape == "id":
            rhs = "number"
        elif shape == "enumeration":
            rhs = " | ".join(_q(c) for c, _, _ in s.constructors)
        elif shape == "record":
            rhs = "readonly [%s]" % ", ".join(em.ts_type(fs) for _, fs in s.constructors[0][1])
        else:
            rhs = " | ".join("readonly [%s]" % ", ".join(["%d" % k] + [em.ts_type(fs) for _, fs in f])
                             for k, (c, f, _) in enumerate(s.constructors))
        lines += ["", "/** The sort %s. */" % name, "export type %s = %s;" % (tn, rhs)]
    for name, s in lps.aliases.items():
        if name not in lps.structs:
            lines += ["", "export type %s = %s;" % (type_name(name), em.ts_type(s))]
    for name, s in lps.structs.items():
        if t.shape[name] == "enumeration":
            lines += ["", "/** The constructors of %s, in declaration order. */" % name,
                      "export const %s_VALUES: readonly %s[] = [%s];" % (
                          type_name(name), type_name(name), ", ".join(_q(c) for c, _, _ in s.constructors))]

    # Maps.
    eqs: dict = {}
    for e in lps.equations:
        h = e.lhs.head if isinstance(e.lhs, App) else e.lhs
        eqs.setdefault(h.name, []).append(e)
    umaps = (st.model if st is not None else model).user_maps
    lines += ["", "// ---- the user maps, walks compiled into loops (docs/spec.md §3.4.8)"]
    allmaps = sorted(umaps, key=lambda m: m.name) + sorted(
        (m for m in lps.maps if m.name.startswith(INIT_PREFIX)), key=lambda m: m.name)
    for m in allmaps:
        lines.append("")
        lines.extend(em.emit_map(m, eqs.get(m.name, [])))
    lines += ["", "const _MAPS: Record<string, (...a: V[]) => V> = {"]
    lines += ["  %s: _m_%s," % (_q(m.name), ident(m.name)) for m in allmaps]
    lines += ["};", "", "/** For tests: the compiled user maps by name, and the stored function. */",
              "export const internals = { maps: _MAPS, Col: _Col };"]

    roles = {p.name: p.role for p in model.params}
    names = [n for n, _ in lps.params]
    config = [n for n in names if roles.get(n) == "config"]
    sorts = dict(lps.params)
    lines += ["", "// ---- the configuration and the state (docs/spec.md §3.8.1)", "",
              "/** The configuration parameters of Core (docs/spec.md §3.6.2). */", "export interface Config {"]
    lines += ["  readonly %s: %s;" % (ident(n), em.ts_type(sorts[n])) for n in config]
    lines += ["}", "", "/** A state: every parameter of Core, read only. A step never changes it; it returns a new one. */",
              "export interface State {"]
    lines += ["  readonly %s: %s;" % (ident(n), em.ts_type(srt)) for n, srt in lps.params]
    lines += ["}", "", "function _initial(config: Config): V {", "  const s: V = {};"]
    fps = fun_params(lps)
    for (n, srt), v in zip(lps.params, lps.init):
        if roles.get(n) == "config":
            val = "config.%s" % ident(n)
        elif n in model.inits:
            val = "_m_gen_init_%s(%s)" % (ident(n), ", ".join("config.%s" % ident(c) for c in config))
        else:
            val = em.expr(v, {})
        r = width_range(model, n)
        if r is not None and n in fps:
            lines.append("  s.%s = %s;" % (ident(n), val))
            lines.append("  s.%s.r = [%d, %d, %s];" % (ident(n), r[0], r[1], _q(n)))
            continue
        if r is not None:
            val = "_fit(%s, %d, %d, %s)" % (val, r[0], r[1], _q(n))
        lines.append("  s.%s = %s;" % (ident(n), val))
    lines += ["  return s;", "}"]

    # Events, actions, entries.
    def union(side, kind):
        rows = []
        for a in side:
            fs = _fields(a)
            rows.append("  | { readonly type: %s%s }" % (
                _q(camel(a.label)), "".join("; readonly %s: %s" % (f, em.ts_type(s)) for f, s in zip(fs, a.sorts))))
        return ["", "/** %s */" % kind, "export type %s =" % ("Event" if side is inputs else "Action")] + (
            rows or ["  never"])
    lines += ["", "// ---- events, actions and entries (docs/spec.md §3.8.10)"]
    lines += union(inputs, "An event: one per input label, named after the label without c_, fields from args.")
    lines[-1] += ";"
    lines += union(outputs, "An action: one per output label.")
    lines[-1] += ";"
    lines += ["", "/** An entry of the action record. */",
              "export type Entry = { readonly event: Event } | { readonly action: Action };", ""]
    for side, nm in ((inputs, "_EV"), (outputs, "_ACT")):
        lines.append("const %s: Record<string, { code: number; label: string; fields: string[]; descs: V[] }> = {" % nm)
        for code, a in enumerate(side):
            lines.append("  %s: { code: %d, label: %s, fields: %s, descs: %s }," % (
                camel(a.label), code, _q(a.label), json.dumps(_fields(a)),
                json.dumps([em.codec_desc(s) for s in a.sorts])))
        lines.append("};")
    lines += ["", "const _EV_BY_CODE = Object.keys(_EV);", "const _ACT_BY_CODE = Object.keys(_ACT);", "",
              "function _args(x: V, table: V): V[] {",
              "  return table[x.type].fields.map((f: string) => x[f]);", "}", "",
              "function _make(type: string, args: V[], table: V): V {",
              "  const x: V = { type };", "  table[type].fields.forEach((f: string, j: number) => {",
              "    x[f] = args[j];", "  });", "  return x;", "}"]
    lines += ['''
/** The bytes of an entry (docs/spec.md §3.8.10), as every backend writes them. */
export function encode(entry: Entry): Uint8Array {
  const out: number[] = [];
  const [side, x, table] = "event" in entry ? [0, entry.event, _EV] : [1, entry.action, _ACT];
  const c = table[x.type];
  out.push(side);
  _enc(c.code, ["n"], out);
  _args(x, table).forEach((v, j) => _enc(v, c.descs[j], out));
  return Uint8Array.from(out);
}

export function decode(data: Uint8Array): Entry {
  if (data.length === 0 || data[0] > 1) throw new DecodeError("a side");
  const side = data[0];
  let [code, pos] = _dec(data, 1, ["n"]);
  const table = side === 0 ? _EV : _ACT;
  const type = (side === 0 ? _EV_BY_CODE : _ACT_BY_CODE)[code];
  if (type === undefined) throw new DecodeError("a label code");
  const args: V[] = [];
  for (const d of table[type].descs) {
    let v: V;
    [v, pos] = _dec(data, pos, d);
    args.push(v);
  }
  if (pos !== data.length) throw new DecodeError("trailing bytes");
  const x = _make(type, args, table);
  return side === 0 ? { event: x } : { action: x };
}

function _show(v: V, d: V): string {
  // A value as mCRL2 prints it, following its shape d.
  const out: string[] = [];
  const stack: V[] = [[v, d]];
  for (;;) {
    const top = stack.pop();
    if (top === undefined) return out.join("");
    const [x, e] = top;
    if (typeof x === "string" && e === null) {
      out.push(x);
    } else if (e[0] === "v") {
      out.push(String(x));
    } else if (e[0] === "id") {
      out.push(e[1] + "(" + x + ")");
    } else if (e[0] === "l" || e[0] === "s") {
      const items: V[] = [[e[0] === "l" ? "[" : "{", null]];
      x.forEach((y: V, j: number) => {
        if (j) items.push([", ", null]);
        items.push([y, e[1]]);
      });
      items.push([e[0] === "l" ? "]" : "}", null]);
      for (let j = items.length - 1; j >= 0; j--) stack.push(items[j]);
    } else {
      const [c, fs, vs] = e[0] === "r" ? [e[1], e[2], x] : [e[1][x[0]][0], e[1][x[0]][1], x.slice(1)];
      const items: V[] = [[c, null]];
      if (fs.length) {
        items.push(["(", null]);
        vs.forEach((y: V, j: number) => {
          if (j) items.push([", ", null]);
          items.push([y, fs[j]]);
        });
        items.push([")", null]);
      }
      for (let j = items.length - 1; j >= 0; j--) stack.push(items[j]);
    }
  }
}''']
    # The label of an entry as mCRL2 prints it.
    lines += ["", "/** The entry as mCRL2 writes its action, ids as the model writes them (job(3)), for comparing state spaces. */",
              "export function mcrl2Label(entry: Entry): string {", "  const x: V = \"event\" in entry ? entry.event : entry.action;",
              "  switch (\"event\" in entry ? \"e:\" + x.type : \"a:\" + x.type) {"]
    for side, pre in ((inputs, "e"), (outputs, "a")):
        for a in side:
            fs = _fields(a)
            parts = [em.label_fn(s, "x.%s" % f) for f, s in zip(fs, a.sorts)]
            text = _q(a.label) if not parts else '%s + "(" + %s + ")"' % (_q(a.label), ' + ", " + '.join(parts))
            lines += ["    case %s:" % _q("%s:%s" % (pre, camel(a.label))), "      return %s;" % text]
    lines += ["  }", '  throw new Error("not an entry of this model");', "}"]

    # The acceptor (§3.8.9).
    lines += ["", "// ---- the acceptor of the unrefined Core (docs/spec.md §3.8.9)"]
    by_key: dict = {}
    for k, s in enumerate(lps.summands):
        kind = model.summand_kind.get(s.name)
        labels = inputs if kind == "input" else outputs
        a = next(x for x in labels if x.label == s.label and len(x.sorts) == len(s.args))
        by_key.setdefault("%s:%s" % ("e" if kind == "input" else "a", camel(a.label)), []).append(k)
        lines += [""] + _emit_summand(em, model, s, str(k), fps)
    lines += ["", "const _SUMMANDS: Record<string, [V, V][]> = {"]
    for key in sorted(by_key):
        lines.append("  %s: [%s]," % (_q(key), ", ".join("[_match%d, _fire%d]" % (k, k) for k in by_key[key])))
    lines += ["};", '''
/** Checks the action record against the unrefined Core (docs/spec.md §3.8.9). */
export class Acceptor {
  #s: V;

  constructor(config: Config) {
    this.#s = _initial(config);
  }

  /** Throws Rejected when no summand accepts the entry, Ambiguous when two do. */
  accept(entry: Entry): void {
    const [key, args] = "event" in entry
      ? ["e:" + entry.event.type, _args(entry.event, _EV)]
      : ["a:" + entry.action.type, _args(entry.action, _ACT)];
    const found: [V, V][] = [];
    for (const [match, fire] of _SUMMANDS[key] ?? []) {
      const env = match(this.#s, args);
      if (env !== null) found.push([fire, env]);
    }
    if (found.length === 0) throw new Rejected(entry);
    if (found.length > 1) throw new Ambiguous(entry);
    const s = { ...this.#s };
    found[0][0](s, found[0][1]);
    this.#s = s;
  }

  /** The acceptor's state. */
  get state(): State {
    return this.#s;
  }
}''']
    if st is not None:
        lines += _emit_stepper(em, model, st, inputs, outputs)
    return "\n".join(lines) + "\n"


def _emit_summand(em: Emitter, model, s, k: str, fps: set) -> list:
    lps = model.lps
    plan = plan_summand(em.types, s, fps, em.fun_maps)
    pscope = {n: "s.%s" % ident(n) for n, _ in lps.params}
    lines = ["function _match%s(s: V, a: V[]): V[] | null {" % k, "  // %s" % s.name]
    scope = dict(pscope)
    sv = {n: "v_" + ident(n) for n, _ in s.sumvars}
    for j, steps, c in plan.tags:
        acc = "a[%d]" % j + "".join(_step(cc, jj) for cc, jj in steps)
        lines.append("  if (%s[0] !== %d) return null;" % (acc, c.index))
    for b in plan.binds:
        acc = "a[%d]" % b.arg + "".join(_step(c, j) for c, j in b.steps)
        lines.append("  const %s = %s;" % (sv[b.var], acc))
        scope[b.var] = sv[b.var]
    for var, e in plan.fixed:
        lines.append("  const %s = %s;" % (sv[var], em.expr(e, scope)))
        scope[var] = sv[var]
    for j, a in enumerate(s.args):
        if isinstance(a, Var) and any(b.var == a.name and b.arg == j and not b.steps for b in plan.binds):
            continue
        lines.append("  if (!_eq(a[%d], %s)) return null;" % (j, em.expr(a, scope)))
    lines.append("  if (!(%s)) return null;" % em.expr(s.cond, scope))
    lines.append("  return [%s];" % ", ".join(sv[n] for n, _ in s.sumvars))
    lines.append("}")
    # The firing: every right side evaluated before anything is written (§3.8.4).
    lines += ["", "function _fire%s(s: V, env: V[]): void {" % k]
    if s.sumvars:
        lines.append("  const [%s] = env;" % ", ".join(sv[n] for n, _ in s.sumvars))
    scope = dict(pscope)
    scope.update({n: sv[n] for n, _ in s.sumvars})
    lines += _fire_body(em, model, s, scope, fps, plan)
    lines.append("}")
    return lines


def _fire_body(em: Emitter, model, s, scope: dict, fps: set, plan) -> list:
    """The next state of a summand: every right side evaluated before anything is
    written (§3.8.4); a stored function is copied before it is written, so that the
    state the step started from is never changed."""
    lines, writes, plans = [], [], {}
    for n, e in s.assigns:
        if n in fps:
            pv = "_p_" + ident(n)
            lines.append("  const %s: V[] = [];" % pv)
            pl: list = []
            em.plan(e, n, scope, pl, "  ", pv)
            lines.extend(pl)
            plans[n] = pv
        else:
            tv = "_n_" + ident(n)
            lines.append("  const %s = %s;" % (tv, em.expr(e, scope)))
            writes.append((n, tv))
    for n in plan.fun_order:
        lines.append("  s.%s = _run(s.%s.clone(), %s);" % (ident(n), ident(n), plans[n]))
    for n, tv in writes:
        r = width_range(model, n)
        lines.append("  s.%s = %s;" % (ident(n), tv if r is None else "_fit(%s, %d, %d, %s)" % (tv, r[0], r[1], _q(n))))
    return lines


def _step(c, j) -> str:
    if c.shape == "id":
        return ""
    return "[%d]" % (j if c.shape == "record" else j + 1)


# ------------------------------------------------------------------ the stepper (docs/spec.md §3.8, §3.18.3)


def _ix_fn(em: Emitter, ix, part: str, extra: str) -> list:
    """function _ix<part>_<name>(s[, v | k]) from the index's equation in the refined Core."""
    args, rhs = ix.eqs[part]
    scope = {}
    for a in args:
        scope[a] = "s.%s" % ident(a[len("ix_p_"):]) if a.startswith("ix_p_") else extra
    head = "function _ix%s_%s(s: V%s): V {" % (part, ix.name, ", %s: V" % extra if extra else "")
    return ["", head, "  return %s;" % em.expr(rhs, scope), "}"]


def _emit_stepper(em: Emitter, model, st, inputs, outputs) -> list:
    m = st.model
    lps = m.lps
    fps = fun_params(lps)
    lines = ["", "// ---- the stepper of the refined Core (docs/spec.md §3.8)"]
    for ix in st.indexes:
        lines += _ix_fn(em, ix, "mem", "v")
        if "cls" in ix.eqs:
            lines += ["", "const _CLS_%s = %s;" % (ix.name, json.dumps(ix.classes))]
            lines += _ix_fn(em, ix, "cls", "v")
            lines += _ix_fn(em, ix, "adm", "k")
        if "key" in ix.eqs:
            lines += _ix_fn(em, ix, "key", "v")
        lines += _ix_fn(em, ix, "bound", "")
        adm = "(c: number) => _ixadm_%s(s, _CLS_%s[c])" % (ix.name, ix.name) if "cls" in ix.eqs else "null"
        cls = "_CLS_%s.indexOf(_ixcls_%s(s, v))" % (ix.name, ix.name) if "cls" in ix.eqs else "0"
        key = "_ixkey_%s(s, v)" % ix.name if "key" in ix.eqs else "0"
        lines += ["", "function _ixfirst_%s(s: V): number {" % ix.name, "  return s._ix_%s.first(%s);" % (ix.name, adm), "}",
                  "", "function _ixsome_%s(s: V): boolean {" % ix.name, "  return s._ix_%s.some(%s);" % (ix.name, adm), "}",
                  "", "function _ixrefresh_%s(s: V, v: number): void {" % ix.name,
                  "  if (_ixmem_%s(s, v)) s._ix_%s.refresh(v, true, %s, %s);" % (ix.name, ix.name, cls, key),
                  "  else s._ix_%s.refresh(v, false, 0, 0);" % ix.name, "}"]
    # The refresh after every summand (§3.8.3): every id below the bound, and every member.
    lines += ["", "function _refresh(s: V): void {"]
    for ix in st.indexes:
        lines += ["  {", "    const ids = new Set<number>(s._ix_%s.ent.keys());" % ix.name,
                  "    for (let v = 0; v < _ixbound_%s(s); v++) ids.add(v);" % ix.name,
                  "    for (const v of [...ids].sort((x, y) => x - y)) _ixrefresh_%s(s, v);" % ix.name, "  }"]
    lines += ["}", "", "function _clone(s: V): V {", "  const c = { ...s };"]
    lines += ["  c._ix_%s = s._ix_%s.clone();" % (ix.name, ix.name) for ix in st.indexes]
    lines += ["  return c;", "}", "", "function _stepper_init(s: V): void {"]
    lines += ["  s._ix_%s = new _Index(%d);" % (ix.name, _ORDERS[ix.order]) for ix in st.indexes]
    lines += ["  _refresh(s);", "}"]
    # Input summands of the refined Core (§3.8.2) and output summands (§3.8.3).
    table_in, table_out = [], []
    for k in st.inputs:
        s = lps.summands[k]
        a = next(x for x in inputs if x.label == s.label and len(x.sorts) == len(s.args))
        lines += [""] + _emit_summand(em, m, s, "_s%d" % k, fps)
        table_in.append((s, a, k))
    pscope = {n: "s.%s" % ident(n) for n, _ in lps.params}
    for j, k in enumerate(st.outputs):
        s = lps.summands[k]
        a = next(x for x in outputs if x.label == s.label and len(x.sorts) == len(s.args))
        plan = plan_summand(em.types, s, fps, em.fun_maps)
        fs = _fields(a)
        act = "{ type: %s%s }" % (_q(camel(a.label)), "".join(", %s: %s" % (f, em.expr(x, pscope)) for f, x in zip(fs, s.args)))
        lines += ["", "function _out%d(s: V, out: Action[]): boolean {" % j, "  // %s" % s.name,
                  "  if (!(%s)) return false;" % em.expr(s.cond, pscope), "  out.push(%s);" % act]
        lines += _fire_body(em, m, s, dict(pscope), fps, plan)
        lines += ["  return true;", "}"]
        table_out.append((s, a, j))
    # One list of each: the step, the output loop and the transition table all read these.
    lines += ["", "const _INS: [string, V, V][] = ["]
    lines += ["  [%s, _match_s%d, _fire_s%d]," % (_q(camel(a.label)), k, k) for s, a, k in table_in]
    lines += ["];", "", "const _OUTS = [%s];" % ", ".join("_out%d" % j for j in range(len(st.outputs))), "",
              "const _STEP: Record<string, [V, V][]> = {};", "for (const [type, match, fire] of _INS) (_STEP[type] ??= []).push([match, fire]);"]
    # The state's encoding, for comparing the backends.
    lines += ["", "/** Every parameter's value, encoded as entries are: equal in every backend for equal states. */",
              "export function stateBytes(state: State): Uint8Array {", "  const s: V = state;", "  const out: number[] = [];"]
    for n, srt in lps.params:
        lines += _enc_param(em, n, srt)
    lines += ["  return Uint8Array.from(out);", "}"]
    stamped = [ix for ix in st.indexes if ix.order in ("newest", "oldest")]
    lines += ["", "/** A key equal for two states exactly when every step from them is the same: the parameters and",
              " * the order of the indexes that remember when an id entered (docs/spec.md §3.18.3). */",
              "export function stateKey(state: State): string {", "  const s: V = state;",
              "  return [...stateBytes(state)].map((b) => b.toString(16).padStart(2, \"0\")).join(\"\")%s;" % "".join(
                  ' + "|" + s._ix_%s.ordered().join(",")' % ix.name for ix in stamped), "}"]
    # The transition table, as data (§3.18.3).
    lines += ['''
/** How an argument's values are enumerated when the state space is explored. */
export type Sort =
  | { readonly kind: "bool" }
  | { readonly kind: "int"; readonly sort: string }
  | { readonly kind: "id"; readonly sort: string; readonly ctor: string }
  | { readonly kind: "enum"; readonly sort: string; readonly values: readonly string[] }
  | { readonly kind: "other"; readonly sort: string };

export interface Arg {
  readonly name: string;
  readonly sort: Sort;
}

/** An input summand of the refined Core: the next state when it accepts the event, else null. */
export interface InputTransition {
  readonly summand: string;
  readonly label: string;
  readonly type: Event["type"];
  readonly args: readonly Arg[];
  next(state: State, event: Event): State | null;
}

/** An output summand of the refined Core, in policy order: the action and the next state when it is enabled. */
export interface OutputTransition {
  readonly summand: string;
  readonly label: string;
  readonly type: Action["type"];
  readonly args: readonly Arg[];
  next(state: State): { readonly state: State; readonly action: Action } | null;
}

/** The result of the constructor or of a step: the state and the actions taken, in order. */
export interface Step {
  readonly state: State;
  readonly actions: readonly Action[];
}''']

    def args_desc(a):
        return "[%s]" % ", ".join("{ name: %s, sort: %s }" % (_q(f), em.sort_desc(srt)) for f, srt in zip(_fields(a), a.sorts))
    lines += ["", "/** The input summands, in the model's order (docs/spec.md §3.18.3). */",
              "export const inputs: readonly InputTransition[] = ["]
    for i, (s, a, k) in enumerate(table_in):
        lines += ["  {", "    summand: %s," % _q(s.name), "    label: %s," % _q(a.label), "    type: %s," % _q(camel(a.label)),
                  "    args: %s," % args_desc(a), "    next(state: State, event: Event): State | null {",
                  "      const [type, match, fire] = _INS[%d];" % i,
                  "      if (event.type !== type) return null;", "      const s = _clone(state);",
                  "      const env = match(s, _args(event, _EV));", "      if (env === null) return null;",
                  "      fire(s, env);", "      _refresh(s);", "      return s;", "    },", "  },"]
    lines += ["];", "", "/** The output summands, in policy order: the first enabled one is taken (docs/spec.md §3.8.3). */",
              "export const outputs: readonly OutputTransition[] = ["]
    for s, a, j in table_out:
        lines += ["  {", "    summand: %s," % _q(s.name), "    label: %s," % _q(a.label), "    type: %s," % _q(camel(a.label)),
                  "    args: %s," % args_desc(a), "    next(state: State) {", "      const s = _clone(state);",
                  "      const out: Action[] = [];", "      if (!_OUTS[%d](s, out)) return null;" % j, "      _refresh(s);",
                  "      return { state: s, action: out[0] };", "    },", "  },"]
    lines += ["];", '''
function _loop(s: V, out: Action[]): void {
  // The output loop (docs/spec.md §3.8.3): the first enabled output in policy order, until none is.
  let n = 0;
  for (;;) {
    if (!_OUTS.some((f) => f(s, out))) return;
    _refresh(s);
    n += 1;
    if (n >= 2 ** 24) throw new StepLimit("2^24 actions in one step");
  }
}

/** The state s0 of docs/spec.md §3.8.1, before the constructor's output loop: where the transition table starts. */
export function start(config: Config): State {
  const s = _initial(config);
  _stepper_init(s);
  return s;
}

/** The initial state and the actions the constructor takes (docs/spec.md §3.8.1). */
export function initial(config: Config): Step {
  const s = _clone(start(config));
  const out: Action[] = [];
  _loop(s, out);
  return { state: s, actions: out };
}

/** Whether the event is accepted in the state (docs/spec.md §3.8.2). */
export function allowed(state: State, event: Event): boolean {
  return (_STEP[event.type] ?? []).some(([match]) => match(state, _args(event, _EV)) !== null);
}

/** Whether no output is enabled: the state every step ends in. */
export function quiescent(state: State): boolean {
  return outputs.every((t) => t.next(state) === null);
}

/** The step (docs/spec.md §3.8.2): the next state and the actions, or null when the event is refused,
 * which changes nothing. Any other error is a StepError and ends the run (docs/spec.md §3.8.5). */
export function step(state: State, event: Event): Step | null {
  const s = _clone(state);
  const args = _args(event, _EV);
  for (const [match, fire] of _STEP[event.type] ?? []) {
    const env = match(s, args);
    if (env !== null) {
      fire(s, env);
      _refresh(s);
      const out: Action[] = [];
      _loop(s, out);
      return { state: s, actions: out };
    }
  }
  return null;
}''']
    return lines


def _enc_param(em: Emitter, n: str, srt) -> list:
    t = em.types
    s = t.resolve(srt)
    a = "s.%s" % ident(n)
    if s[0] == "->":
        kd, vd = json.dumps(em.codec_desc(s[1][0])), json.dumps(em.codec_desc(s[2]))
        return ["  _enc(%s.d, %s, out);" % (a, vd), "  _enc(%s.m.size, [\"n\"], out);" % a,
                "  for (const [k, v] of %s.entries()) {" % a, "    _enc(k, %s, out);" % kd, "    _enc(v, %s, out);" % vd, "  }"]
    if s[0] == "FSet":
        return ["  _enc(%s, %s, out);" % (a, json.dumps(["l", em.codec_desc(s[1])]))]
    return ["  _enc(%s, %s, out);" % (a, json.dumps(em.codec_desc(s)))]


# ------------------------------------------------------------------ the Svelte module (docs/spec.md §3.18.4)


def _svelte(model, st, module: str) -> str:
    em = Emitter(st.model)
    lps = model.lps
    inputs, outputs = _labels(model)
    cn = class_name(module)
    port, view = cn + "Port", cn + "View"
    sorts = dict(lps.params)
    types = sorted({type_name(n) for n in lps.structs} | {type_name(n) for n in lps.aliases if n not in lps.structs})
    lines = [HEADER.rstrip("\n"),
             "// The model's stepper as a Svelte 5 state machine (docs/spec.md §3.18.4). Components read `view` and call",
             "// one method per input label; the machine calls the port for every output action, and the results of what",
             "// the port does come back as input methods. The logic is all in %s.ts; nothing here decides anything." % module,
             'import * as M from "./%s.ts";' % module,
             "import type { Action, Config, Event, Fn, State } from \"./%s.ts\";" % module]
    if types:
        lines.append("import type { %s } from \"./%s.ts\";" % (", ".join(types), module))
    lines += ["", "export type { Action, Config, Event, Fn, State };"]
    if types:
        lines.append("export type { %s };" % ", ".join(types))
    lines += ["", "/** What the machine asks the outside world to do: one method per output action. Called after the",
              " * state has changed, in the order the model takes the actions. */", "export interface %s {" % port]
    used = set()
    for a in outputs:
        n = camel(a.label)
        n = n + "_" if n == "refused" else n
        used.add(n)
        fs = _fields(a)
        lines.append("  %s(%s): void;" % (n, ", ".join("%s: %s" % (f, em.ts_type(s)) for f, s in zip(fs, a.sorts))))
    lines += ["  /** An input the model does not allow now was called; nothing changed. */",
              "  refused?(event: Event): void;", "}", "",
              "/** The state, read only: one property per parameter of the model. */", "export interface %s {" % view]
    lines += ["  readonly %s: %s;" % (ident(n), em.ts_type(srt)) for n, srt in lps.params]
    lines += ["}", "", "class _Fields implements %s {" % view]
    lines += ["  #%s: %s;" % (ident(n), em.ts_type(srt)) for n, srt in lps.params]
    lines += ["", "  constructor(s: State) {"]
    lines += ["    this.#%s = $state.raw(s.%s);" % (ident(n), ident(n)) for n, _ in lps.params]
    lines += ["  }", ""]
    for n, srt in lps.params:
        lines += ["  get %s(): %s {" % (ident(n), em.ts_type(srt)), "    return this.#%s;" % ident(n), "  }", ""]
    lines += ["  __show(s: State): void {"]
    lines += ["    this.#%s = s.%s;" % (ident(n), ident(n)) for n, _ in lps.params]
    lines += ["  }", "}", "",
              "/** The machine: the state as reactive fields, one method per input label (docs/spec.md §3.18.4). */",
              "export class %s {" % cn,
              "  /** The state, for components: read only; it changes only through the input methods. */",
              "  readonly view: %s;" % view, "  readonly #fields: _Fields;", "  readonly #port: %s;" % port,
              "  #state: State;", "  #error: M.StepError | null;", "  #queue: Action[] = [];", "  #busy = false;",
              "", "  constructor(config: Config, port: %s) {" % port, "    this.#port = port;",
              "    const r = M.initial(config);",
              "    // Reactive too, so that what reads allowed() or stopped is updated with the fields.",
              "    this.#state = $state.raw(r.state);", "    this.#error = $state.raw(null);",
              "    this.#fields = new _Fields(r.state);", "    this.view = this.#fields;", "    this.#emit(r.actions);",
              "  }", "",
              "  /** The error that stopped the machine, if one did (docs/spec.md §3.8.5); then every input is refused. */",
              "  get stopped(): M.StepError | null {", "    return this.#error;", "  }", "",
              "  /** Whether the event is allowed now: what a component enables. */",
              "  allowed(event: Event): boolean {", "    return this.#error === null && M.allowed(this.#state, event);", "  }"]
    for a in inputs:
        n = camel(a.label)
        n = n + "_" if n in _MEMBERS else n
        fs = _fields(a)
        lines += ["", "  /** The input %s: false, and nothing changes, when the model does not allow it now. */" % a.label,
                  "  %s(%s): boolean {" % (n, ", ".join("%s: %s" % (f, em.ts_type(s)) for f, s in zip(fs, a.sorts))),
                  "    return this.#input({ type: %s%s });" % (_q(camel(a.label)), "".join(", %s" % f for f in fs)), "  }"]
    lines += ["", "  #input(event: Event): boolean {", "    let r: M.Step | null = null;", "    if (this.#error === null) {",
              "      try {", "        r = M.step(this.#state, event);", "      } catch (e) {",
              "        if (!(e instanceof M.StepError)) throw e;", "        this.#error = e;", "        return false;", "      }", "    }",
              "    if (r === null) {", "      this.#port.refused?.(event);", "      return false;", "    }",
              "    this.#state = r.state;", "    this.#fields.__show(r.state);", "    this.#emit(r.actions);", "    return true;",
              "  }", "", "  #emit(actions: readonly Action[]): void {",
              "    // A port may call an input method while it is called: its actions wait for the ones before them.",
              "    this.#queue.push(...actions);", "    if (this.#busy) return;", "    this.#busy = true;", "    try {",
              "      for (let a = this.#queue.shift(); a !== undefined; a = this.#queue.shift()) {",
              "        switch (a.type) {"]
    for a in outputs:
        n = camel(a.label)
        pn = n + "_" if n == "refused" else n
        lines += ["          case %s:" % _q(n),
                  "            this.#port.%s(%s);" % (pn, ", ".join("a.%s" % f for f in _fields(a))), "            break;"]
    lines += ["        }", "      }", "    } finally {", "      this.#busy = false;", "    }", "  }", "}"]
    _ = sorts
    return "\n".join(lines) + "\n"
