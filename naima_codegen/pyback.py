"""The Python backend: the package `<generated>/python/<python_package>`
(docs/spec.md §3.8, §3.12), one of the three backends a project chooses from.

It emits the configuration, the initial state (§3.8.1, `init` included), the
user maps and walks compiled into loops (§3.4.8), the acceptor of the unrefined
Core (§3.8.9), the encoding of entries (§3.8.10) and, when the model has a
policy (§3.7), the stepper of the refined Core with its indexes (§3.8, §3.9.9).
The catalogue's representations (§3.10.3) are not implemented yet, in any backend.

Values: Bool and integers are Python's; an id sort is its Nat, an enumeration
the index of its constructor, a record the tuple of its fields, a union the
tuple (constructor index, fields...); a list is a tuple, a finite set a
frozenset; a function is a view: `_Col` (a stored function, default plus
entries), `_Upd` (one update over a view), `_Ov` (an overlay, §3.4.8).

The generated code has no recursion: walks are loops, and every compilation
step here is a loop over an explicit stack.
"""

from __future__ import annotations

import re

from .expr import App, Bind, Num, Op, Var, Whr, show
from .frontend import INIT_PREFIX
from .functions import is_fun, mentions
from .sortedlists import Sorted
from .lower import Types, codec_desc, fun_params, g10, plan_summand, width_range

from .header import block as _header_block  # noqa: E402
HEADER = _header_block("#")

RUNTIME = '''
import heapq

class StepError(Exception):
    """An error of the generated code (docs/spec.md §3.14.2)."""


class Undefined(StepError):
    pass


class Overflow(StepError):
    pass


def _fit(v, lo, hi, name):
    """A value written to a parameter fits its width (docs/spec.md §3.12.4)."""
    if v < lo or v > hi:
        raise Overflow("%s = %d does not fit [%d, %d]" % (name, v, lo, hi))
    return v


class Refused(StepError):
    """No input summand accepts the event; the state is unchanged (docs/spec.md §3.8.2)."""


class StepLimit(StepError):
    pass


class NotQuiescent(StepError):
    pass


class AuditFailed(Exception):
    """An index differs from its definition (docs/spec.md §3.9.9): tests only."""


class Reject(Exception):
    pass


def _least(w):
    """least(W) (docs/spec.md §3.7.6)."""
    if not w:
        raise Undefined("least of an empty set")
    return min(w)


# Whether the state being stepped audits (docs/spec.md §3.9.9): set by _stepper_init.
_AUDIT = [False]


def _place(t, u):
    """The least i with t[i] >= u in a tuple sorted by id: a binary search."""
    lo, hi = 0, len(t)
    if hi and t[hi - 1] < u:
        return hi
    while lo < hi:
        mid = (lo + hi) // 2
        if t[mid] < u:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _ins_sorted(u, t, walk):
    """An ordered insert into a tuple sorted by id (docs/spec.md §3.10.6), at the place
    a binary search finds; with the audit on, checked against the model's walk."""
    i = _place(t, u)
    r = t if i < len(t) and t[i] == u else t[:i] + (u,) + t[i:]
    if _AUDIT[0] and r != walk(u, t):
        raise AuditFailed("an ordered insert differs from the model's walk")
    return r


def _merge_sorted(l, t, walk):
    """An ordered merge of l into a tuple sorted by id (docs/spec.md §3.10.6)."""
    r = t
    for u in l:
        i = _place(r, u)
        if not (i < len(r) and r[i] == u):
            r = r[:i] + (u,) + r[i:]
    if _AUDIT[0] and r != walk(l, t):
        raise AuditFailed("an ordered merge differs from the model's walk")
    return r


class _Index:
    """An index of the policy (docs/spec.md §3.7.6, §3.10.3): per class a heap
    of (order key, id) with lazy deletion; an entry is valid while its id is a
    member of that class with that order key. Order keys: the id (least), the
    key's value (key), minus the entry stamp (newest), the stamp (oldest), the
    order key of the index it is within (within)."""

    __slots__ = ("order", "ent", "heaps", "stamp")

    def __init__(self, order, ncls):
        self.order = order  # 0 least, 1 key, 2 newest, 3 oldest, 4 within
        self.ent = {}  # id -> (class, order key)
        self.heaps = [[] for _ in range(ncls)]
        self.stamp = 0

    def refresh(self, v, m, c, k):
        old = self.ent.get(v)
        if not m:
            if old is not None:
                del self.ent[v]
            return
        o = self.order
        if o == 0:
            ok = v
        elif o in (1, 4):
            ok = k
        elif old is not None:
            ok = old[1]  # a member keeps its stamp, across a change of class too
        else:
            self.stamp += 1
            ok = -self.stamp if o == 2 else self.stamp
        new = (c, ok)
        if old != new:
            self.ent[v] = new
            h = self.heaps[c]
            heapq.heappush(h, (ok, v))
            if len(h) > 2 * len(self.ent) + 16:
                h[:] = [(e[1], x) for x, e in self.ent.items() if e[0] == c]
                heapq.heapify(h)

    def _front(self, c):
        h = self.heaps[c]
        ent = self.ent
        while h:
            ok, v = h[0]
            e = ent.get(v)
            if e is not None and e[0] == c and e[1] == ok:
                return h[0]
            heapq.heappop(h)
        return None

    def first(self, adm):
        best = None
        for c in range(len(self.heaps)):
            if adm is not None and not adm(c):
                continue
            f = self._front(c)
            if f is not None and (best is None or f < best):
                best = f
        if best is None:
            raise Undefined("first of an index without an admitted member")
        return best[1]

    def some(self, adm):
        for c in range(len(self.heaps)):
            if (adm is None or adm(c)) and self._front(c) is not None:
                return True
        return False

    def ordered(self):
        """The members in index order (for the audit)."""
        return [v for v, _ in sorted(self.ent.items(), key=lambda it: (it[1][1], it[0]))]


class Rejected(Reject):
    pass


class Ambiguous(Reject):
    pass


class _Col:
    """A stored function: a default and the entries that differ from it."""

    __slots__ = ("d", "m", "r", "w")

    def __init__(self, d, r=None):
        self.d = d
        self.m = {}
        self.r = r  # (lo, hi, name): the width its values fit (§3.12.4)
        self.w = None  # the list of dirty ids, when an index reads this column (docs/spec.md §3.8.3)

    def get(self, k):
        return self.m.get(k, self.d)

    def set(self, k, v):
        if self.r is not None:
            _fit(v, *self.r)
        if self.w is not None:
            self.w.append(k)
        if v == self.d:
            self.m.pop(k, None)
        else:
            self.m[k] = v


class _Upd:
    """f[k -> v] over a view, read only."""

    __slots__ = ("b", "k", "v")

    def __init__(self, b, k, v):
        self.b, self.k, self.v = b, k, v

    def get(self, x):
        return self.v if x == self.k else self.b.get(x)


class _Ov:
    """An overlay (docs/spec.md §3.4.8): the entries written during a call over an unchanged view."""

    __slots__ = ("b", "m")

    def __init__(self, b):
        self.b = b
        self.m = {}

    def get(self, x):
        m = self.m
        return m[x] if x in m else self.b.get(x)

    def set(self, k, v):
        self.m[k] = v


def _run(view, plan):
    for op in plan:
        if op[0] == 0:
            view.set(op[1], op[2])
        else:
            op[1](view)
    return view


def _head(l):
    if not l:
        raise Undefined("head of []")
    return l[0]


def _tail(l):
    if not l:
        raise Undefined("tail of []")
    return l[1:]


def _rhead(l):
    if not l:
        raise Undefined("rhead of []")
    return l[-1]


def _rtail(l):
    if not l:
        raise Undefined("rtail of []")
    return l[:-1]


def _at(l, i):
    if i >= len(l):
        raise Undefined("element %d of a list of %d" % (i, len(l)))
    return l[i]


def _int2nat(x):
    if x < 0:
        raise Undefined("Int2Nat(%d)" % x)
    return x


def _nat2pos(x):
    if x <= 0:
        raise Undefined("Nat2Pos(%d)" % x)
    return x


class DecodeError(Exception):
    pass


def _enc(v, d, out):
    stack = [(v, d)]
    while stack:
        v, d = stack.pop()
        k = d[0]
        if k == "b":
            out.append(1 if v else 0)
        elif k in ("n", "i"):
            if k == "i":
                v = (v << 1) if v >= 0 else ((-v << 1) - 1)
            if v < 0:
                raise StepError("a negative Nat in an entry")
            while True:
                b = v & 0x7F
                v >>= 7
                if v:
                    out.append(b | 0x80)
                else:
                    out.append(b)
                    break
        elif k == "l":
            stack.extend((x, d[1]) for x in reversed(v))
            stack.append((len(v), ("n",)))
        elif k == "r":
            stack.extend(zip(reversed(v), reversed(d[1])))
        else:
            f = d[1][v[0]]
            stack.extend(zip(reversed(v[1:]), reversed(f)))
            stack.append((v[0], ("n",)))


def _dec(data, pos, d):
    """(value, next position): frames on an explicit stack, values on another."""
    vals = []
    stack = [("v", d)]
    while stack:
        op = stack.pop()
        if op[0] == "v":
            k = op[1][0]
            if k == "b":
                if pos >= len(data) or data[pos] > 1:
                    raise DecodeError("a Bool")
                vals.append(data[pos] == 1)
                pos += 1
            elif k in ("n", "i"):
                x, sh = 0, 0
                while True:
                    if pos >= len(data) or sh > 140:
                        raise DecodeError("a number")
                    b = data[pos]
                    pos += 1
                    x |= (b & 0x7F) << sh
                    sh += 7
                    if not b & 0x80:
                        break
                vals.append((x >> 1) ^ -(x & 1) if k == "i" else x)
            elif k == "l":
                stack.append(("list", op[1][1]))
                stack.append(("v", ("n",)))
            elif k == "r":
                stack.append(("tuple", len(op[1][1]), None))
                stack.extend(("v", f) for f in reversed(op[1][1]))
            else:
                stack.append(("union", op[1]))
                stack.append(("v", ("n",)))
        elif op[0] == "list":
            n = vals.pop()
            stack.append(("tuple", n, None))
            stack.extend(("v", op[1]) for _ in range(n))
        elif op[0] == "union":
            c = vals.pop()
            if c >= len(op[1][1]):
                raise DecodeError("a constructor index")
            f = op[1][1][c]
            stack.append(("tuple", len(f), c))
            stack.extend(("v", x) for x in reversed(f))
        else:
            n, c = op[1], op[2]
            items = vals[len(vals) - n:] if n else []
            del vals[len(vals) - n:]
            vals.append(tuple(items) if c is None else (c,) + tuple(items))
    return vals[0], pos


def encode(entry):
    """The bytes of an entry (docs/spec.md §3.8.10)."""
    side, item = entry
    out = bytearray([side])
    _enc(item[0], ("n",), out)
    for v, d in zip(item[1:], _ARGS[(side, item[0])]):
        _enc(v, d, out)
    return bytes(out)


def decode(data):
    if not data or data[0] > 1:
        raise DecodeError("a side")
    side = data[0]
    code, pos = _dec(data, 1, ("n",))
    if (side, code) not in _ARGS:
        raise DecodeError("a label code")
    args = []
    for d in _ARGS[(side, code)]:
        v, pos = _dec(data, pos, d)
        args.append(v)
    if pos != len(data):
        raise DecodeError("trailing bytes")
    return (side, (code,) + tuple(args))
'''

_KEYWORDS = {"and", "or", "not", "if", "else", "in", "is", "for", "while", "lambda", "def", "class", "return", "None",
             "True", "False", "pass", "break", "continue", "import", "from", "as", "with", "yield", "global", "del",
             "try", "except", "finally", "raise", "assert", "async", "await", "elif", "nonlocal", "match", "case"}


def ident(name: str) -> str:
    """A Python identifier for a model name (§3.3.8: a reserved word gets a trailing underscore)."""
    n = re.sub(r"[^A-Za-z0-9_]", lambda m: "_q" if m.group(0) == "'" else "_", name)
    return n + "_" if n in _KEYWORDS else n


class Emitter:
    """Compiles expressions of the accepted model into Python."""

    def __init__(self, model):
        self.model = model
        self.lps = model.lps
        self.types = Types(model.lps, model.config.id_sorts)
        self.maps = {m.name: m for m in model.lps.maps}
        self.fun_maps = model.fun_maps
        self.walks = model.walks
        self.tmp = 0
        # sorted per-id lists (docs/spec.md §3.10.6), set by emit
        self.sorted = Sorted([model.lps], {}, {}, ())

    def fresh(self, base: str = "t") -> str:
        self.tmp += 1
        return "_%s%d" % (base, self.tmp)

    # ------------------------------------------------------------ expressions

    def expr(self, e, scope: dict) -> str:
        """Python for e; function-sorted subexpressions become views."""
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
                out.append("(lambda %s: %s)(%s)" % (", ".join(names[v.name] for v, _ in x.defs), body, ", ".join(vals)))
                continue
            # App
            h = x.head
            hn = h.name if isinstance(h, Op) else None
            if hn == "if" and len(x.args) == 3:
                if not seen:
                    stack.append((x, True))
                    for a in reversed(x.args):
                        stack.append((a, False))
                    continue
                c, a, b = out.pop(-3), out.pop(-2), out.pop()
                out.append("(%s if %s else %s)" % (a, c, b))
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
                out.append("%s.get(%s)" % (f, args[0]) if n == 1 else "%s.get((%s,))" % (f, ", ".join(args)))
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
        if n == "true":
            return "True"
        if n == "false":
            return "False"
        if n == "[]":
            return "()"
        if n in ("{}", "@fset_empty"):
            return "frozenset()"
        if n in self.types.cons:
            c = self.types.cons[n]
            if c.shape == "enumeration":
                return str(c.index)
            if c.shape == "union":
                return "(%d,)" % c.index
            return "()"
        if n in self.maps:
            return "_m_%s()" % ident(n)
        raise g10("operator %s" % n, "no Python for this constant")

    def _bind(self, x: Bind, scope: dict) -> str:
        if x.kind == "Lambda":
            if len(x.vars) != 1:
                raise g10("lambda", "a lambda over %d variables" % len(x.vars))
            return "_Col(%s)" % self.expr(x.body, scope)
        if x.kind in ("Exists", "Forall") and len(x.vars) == 1:
            v = x.vars[0].name
            body = x.body
            mem, rest = body.args[0], body.args[1]
            col = mem.args[1]
            name = ident("q_" + v) + self.fresh("")
            inner = dict(scope)
            inner[v] = name
            c = self.expr(col, scope)
            r = self.expr(rest, inner)
            return ("any(%s for %s in %s)" if x.kind == "Exists" else "all(%s for %s in %s)") % (r, name, c)
        raise g10("binder %s" % x.kind, "no Python for it here")

    def _app(self, x, hn, a) -> str:
        t = self.types
        sorts = x.head.sort
        dom = sorts[1] if sorts[0] == "->" else ()
        k0 = t.kind(dom[0]) if dom else None
        if hn in t.cons:
            c = t.cons[hn]
            if c.shape == "id":
                return a[0]
            if c.shape == "record":
                return "(%s,)" % ", ".join(a)
            return "(%d, %s)" % (c.index, ", ".join(a))
        if hn in t.proj:
            cands = t.proj[hn]
            c, j = cands[0]
            if c.shape == "id":
                return a[0]
            if len(cands) > 1 and len({(cc.shape, jj) for cc, jj in cands}) > 1:
                raise g10("projection %s" % hn, "a projection shared by constructors at different positions")
            return "%s[%d]" % (a[0], j if c.shape == "record" else j + 1)
        if hn in t.recog:
            return "(%s[0] == %d)" % (a[0], t.recog[hn].index)
        if hn in self.maps and hn not in ("if",):
            return "_m_%s(%s)" % (ident(hn), ", ".join(a))
        binary = {"&&": "and", "||": "or", "==": "==", "!=": "!=", "<": "<", "<=": "<=", ">": ">", ">=": ">=",
                  "mod": "%", "++": "+", "in": "in"}
        if hn in binary and len(a) == 2:
            return "(%s %s %s)" % (a[0], binary[hn], a[1])
        if hn == "=>":
            return "((not %s) or %s)" % (a[0], a[1])
        if hn == "!":
            return "(not %s)" % a[0]
        if hn == "+" and len(a) == 2:
            return "(%s | %s)" % (a[0], a[1]) if k0 == "FSet" else "(%s + %s)" % (a[0], a[1])
        if hn == "-" and len(a) == 2:
            return "(%s - %s)" % (a[0], a[1])
        if hn == "-" and len(a) == 1:
            return "(-%s)" % a[0]
        if hn == "*" and len(a) == 2:
            return "(%s & %s)" % (a[0], a[1]) if k0 == "FSet" else "(%s * %s)" % (a[0], a[1])
        if hn == "div":
            return "(%s // %s)" % (a[0], a[1])
        if hn in ("min", "max"):
            return "%s(%s, %s)" % (hn, a[0], a[1])
        if hn == "succ":
            return "(%s + 1)" % a[0]
        if hn == "pred":
            return "(%s - 1)" % a[0]
        if hn == "abs":
            return "abs(%s)" % a[0]
        if hn in ("Pos2Nat", "Nat2Int", "Pos2Int", "@cNat", "@cInt"):
            return a[0]
        if hn == "@cNeg":
            return "(-%s)" % a[0]
        if hn == "Int2Nat":
            return "_int2nat(%s)" % a[0]
        if hn == "Nat2Pos":
            return "_nat2pos(%s)" % a[0]
        if hn == "|>":
            return "((%s,) + %s)" % (a[0], a[1])
        if hn == "<|":
            return "(%s + (%s,))" % (a[0], a[1])
        if hn == "head":
            return "_head(%s)" % a[0]
        if hn == "tail":
            return "_tail(%s)" % a[0]
        if hn == "rhead":
            return "_rhead(%s)" % a[0]
        if hn == "rtail":
            return "_rtail(%s)" % a[0]
        if hn == "#":
            return "len(%s)" % a[0]
        if hn == ".":
            return "_at(%s, %s)" % (a[0], a[1])
        if hn in ("@fset_cons", "@fset_insert"):
            return "(%s | frozenset((%s,)))" % (a[1], a[0])
        if hn == "@func_update":
            return "_Upd(%s, %s, %s)" % (a[0], a[1], a[2])
        raise g10("operator %s" % hn, "no Python for it")

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
        overlay. With `eager`, the arguments that are not functions are bound as
        default values of the lambda the call goes in, so they are evaluated when
        the plan is made, before anything is written (§3.8.4): (call, defaults)."""
        name = x.head.name
        k = self.fun_maps.get(name, ((), None))[1]
        fpos = self.fun_maps.get(name, ((), None))[0]
        args, defaults = [], ""
        for j, a in enumerate(x.args):
            s = self.expr(a, scope) if not (j == k and target is not None) else target
            if j == k and target is not None:
                pass
            elif j in self.written(name):
                s = "_Ov(%s)" % s
            elif eager and j not in fpos:
                d = "_a%d" % j
                defaults += ", %s=%s" % (d, s)
                s = d
            args.append(s)
        text = "_m_%s(%s)" % (ident(name), ", ".join(args))
        return (text, defaults) if eager else text

    def plan(self, fe, root: str, scope: dict, lines: list, indent: str, planvar: str, sorted_root: bool = False):
        """Lines that append to `planvar` the writes of a function expression built
        from `root` (§3.4.8), evaluating every index, value and condition now,
        before anything is written (§3.8.4). With `sorted_root` (root a sorted
        column, or a sorted walk's parameter, docs/spec.md §3.10.6), an ordered insert
        or merge into root's own entry is done by a binary search."""
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
                up = self.sorted.update_value(x.args[2], x.args[1], root) \
                    if sorted_root and isinstance(x.args[0], Var) and x.args[0].name == root else None
                if up is not None:
                    fn = "_ins_sorted" if up[0] == "ins" else "_merge_sorted"
                    v = "%s(%s, %s, _m_%s)" % (fn, self.expr(up[1], scope), self.expr(x.args[2].args[1], scope),
                                               ident(x.args[2].head.name))
                else:
                    v = self.expr(x.args[2], scope)
                stack.append(("line", "%s.append((0, %s, %s))" % (planvar, i, v), ind))
                stack.append(("fe", x.args[0], ind))
            elif h == "if":
                c = self.expr(x.args[0], scope)
                # Popped in this order: if, pass, then-branch, else, pass, else-branch
                # (a `pass` keeps a branch that writes nothing valid).
                stack.append(("fe", x.args[2], ind + "    "))
                stack.append(("line", "pass", ind + "    "))
                stack.append(("line", "else:", ind))
                stack.append(("fe", x.args[1], ind + "    "))
                stack.append(("line", "pass", ind + "    "))
                stack.append(("line", "if %s:" % c, ind))
            elif h in self.fun_maps and self.fun_maps[h][1] is not None:
                k = self.fun_maps[h][1]
                body, defaults = self.call(x, scope, target="_tgt", eager=True)
                stack.append(("line", "%s.append((1, lambda _tgt%s: %s))" % (planvar, defaults, body), ind))
                stack.append(("fe", x.args[k], ind))
            else:
                raise g10("function expression %s" % show(x)[:80], "not built from %s" % root)
        return lines

    # ------------------------------------------------------------ maps and walks

    def emit_map(self, m, eqs) -> list:
        name = m.name
        if name in self.walks:
            return self.emit_walk(m, self.walks[name])
        lines = []
        nargs = m.arity
        params = ["a%d" % j for j in range(nargs)]
        lines.append("def _m_%s(%s):" % (ident(name), ", ".join(params)))
        ret = self.fun_maps.get(name, ((), None))[1]
        for e in eqs:
            lhs = e.lhs
            if not isinstance(lhs, App):
                lines.append("    return %s" % self.expr(e.rhs, {}))
                continue
            scope, cond = {}, []
            for j, p in enumerate(lhs.args):
                if isinstance(p, Var):
                    scope[p.name] = params[j]
                    continue
                c = self.types.cons[p.head.name if isinstance(p, App) else p.name]
                if c.shape == "enumeration":
                    cond.append("%s == %d" % (params[j], c.index))
                elif c.shape == "union":
                    cond.append("%s[0] == %d" % (params[j], c.index))
                if isinstance(p, App):
                    for k, v in enumerate(p.args):
                        scope[v.name] = params[j] if c.shape == "id" else "%s[%d]" % (params[j], k if c.shape == "record" else k + 1)
            ind = "    "
            if cond:
                lines.append("    if %s:" % " and ".join(cond))
                ind = "        "
            if ret is not None:
                pl = []
                self.plan(e.rhs, lhs.args[ret].name, scope, pl, ind, "_p")
                lines.append(ind + "_p = []")
                lines.extend(pl)
                lines.append(ind + "return _run(%s, _p)" % params[ret])
            else:
                lines.append(ind + "return %s" % self.expr(e.rhs, scope))
        if len(eqs) > 1:
            lines.append("    raise Undefined(%r)" % ("no equation of %s applies" % name))
        return lines

    def emit_walk(self, m, w) -> list:
        """A walk compiled into one loop (§3.4.8)."""
        n = len(w.params)
        lines = ["def _m_%s(%s):" % (ident(m.name), ", ".join("a%d" % j for j in range(n)))]
        var = {p: "v_" + ident(p) for p in w.params}
        for j, p in enumerate(w.params):
            lines.append("    %s = a%d" % (var[p], j))
        lst = var[w.tail]
        lines.append("    _i = 0")
        lines.append("    _n = len(%s)" % lst)
        if w.op == "+":
            lines.append("    _acc = 0")
        elif w.op == "|>":
            lines.append("    _pre = []")
        lines.append("    while True:")
        base_scope = {p: var[p] for p in w.params}
        base_scope[w.tail] = "()"
        lines.append("        if _i == _n:")
        lines.extend(self._walk_exit(w, w.base, base_scope, "            "))
        lines.append("            break")
        scope = {p: var[p] for p in w.params}
        scope[w.head] = "_x"
        scope[w.tail] = "%s[_i + 1:]" % lst
        lines.append("        _x = %s[_i]" % lst)
        stack = [("R", w.body, "        ", scope)]
        while stack:
            kind, x, ind, sc = stack.pop()
            if kind == "line":
                lines.append(ind + x)
                continue
            if not mentions(x, m.name):
                lines.extend(self._walk_exit(w, x, sc, ind))
                lines.append(ind + "break")
                continue
            if isinstance(x, Whr):
                sc2 = dict(sc)
                for v, d in x.defs:
                    nm = ident("w_" + v.name) + self.fresh("")
                    lines.append(ind + "%s = %s" % (nm, self.expr(d, sc)))
                    sc2[v.name] = nm
                stack.append(("R", x.body, ind, sc2))
                continue
            h = x.head.name
            if h == m.name:
                lines.extend(self._walk_continue(w, m, x, sc, ind))
                continue
            if h == "if":
                c = self.expr(x.args[0], sc)
                stack.append(("R", x.args[2], ind + "    ", sc))
                stack.append(("line", "else:", ind, None))
                stack.append(("R", x.args[1], ind + "    ", sc))
                lines.append(ind + "if %s:" % c)
                continue
            left = not mentions(x.args[0], m.name)
            e, r = (x.args[0], x.args[1]) if left else (x.args[1], x.args[0])
            ev = self.expr(e, sc)
            if h == "+":
                lines.append(ind + "_acc += %s" % ev)
            elif h == "||":
                lines.append(ind + "if %s:" % ev)
                lines.append(ind + "    _r = True")
                lines.append(ind + "    break")
            elif h == "&&":
                lines.append(ind + "if not %s:" % ev)
                lines.append(ind + "    _r = False")
                lines.append(ind + "    break")
            elif h == "|>":
                lines.append(ind + "_pre.append(%s)" % ev)
            stack.append(("R", r, ind, sc))
        if w.op == "+":
            lines.append("    return _acc + _r")
        elif w.op == "|>":
            lines.append("    return tuple(_pre) + _r")
        else:
            lines.append("    return _r")
        return lines

    def _walk_exit(self, w, x, scope, ind) -> list:
        if w.ret_pos is None:
            return [ind + "_r = %s" % self.expr(x, scope)]
        root = w.params[w.ret_pos]
        pl = []
        self.plan(x, root, scope, pl, ind, "_p", sorted_root=self.sorted.walks.get(w.name) == w.ret_pos)
        return [ind + "_p = []"] + pl + [ind + "_r = _run(%s, _p)" % scope[root]]

    def _walk_continue(self, w, m, x, scope, ind) -> list:
        lines = []
        temps = []
        plans = []
        for j, (p, a) in enumerate(zip(w.params, x.args)):
            if j == w.pos:
                continue
            if is_fun(self.lps, m.sort[1][j]):
                if isinstance(a, Var) and a.name == p:
                    continue
                pv = self.fresh("p")
                lines.append(ind + "%s = []" % pv)
                pl = []
                self.plan(a, p, scope, pl, ind, pv, sorted_root=self.sorted.walks.get(m.name) == j)
                lines.extend(pl)
                plans.append((scope[p], pv))
            else:
                if isinstance(a, Var) and a.name == p:
                    continue
                t = self.fresh()
                lines.append(ind + "%s = %s" % (t, self.expr(a, scope)))
                temps.append((scope[p], t))
        for v, pv in plans:
            lines.append(ind + "_run(%s, %s)" % (v, pv))
        for v, t in temps:
            lines.append(ind + "%s = %s" % (v, t))
        lines.append(ind + "_i += 1")
        lines.append(ind + "continue")
        return lines


def emit(model, st=None) -> str:
    """The text of <generated>/python/<python_package>/__init__.py; with st (a
    `stepper.Stepper`), the stepper of the refined Core too (docs/spec.md §3.8)."""
    em = Emitter(st.model if st is not None else model)
    em.sorted = Sorted([model.lps] + ([st.model.lps] if st is not None else []), em.walks, em.fun_maps,
                       model.config.id_sorts)
    lps = model.lps
    lines = [HEADER.rstrip("\n"), '"""The model\'s generated code as Python (docs/spec.md §3.12)."""', RUNTIME]
    eqs: dict = {}
    for e in lps.equations:
        h = e.lhs.head if isinstance(e.lhs, App) else e.lhs
        eqs.setdefault(h.name, []).append(e)
    umaps = (st.model if st is not None else model).user_maps
    # the user's maps by name, then the generator's init maps by name (§3.6.2)
    for m in sorted(umaps, key=lambda m: m.name) + sorted((m for m in lps.maps if m.name.startswith(INIT_PREFIX)),
                                                         key=lambda m: m.name):
        lines.append("")
        lines.append("")
        lines.extend(em.emit_map(m, eqs.get(m.name, [])))

    roles = {p.name: p.role for p in model.params}
    names = [n for n, _ in lps.params]
    config = [n for n in names if roles.get(n) == "config"]
    lines += ["", "", "class Config:", '    """The configuration parameters of Core (§3.6.2)."""', "",
              "    __slots__ = (%s)" % "".join("%r, " % ident(n) for n in config), "",
              "    def __init__(self, %s):" % ", ".join(ident(n) for n in config)]
    lines += ["        self.%s = %s" % (ident(n), ident(n)) for n in config] or ["        pass"]
    # The initial state (§3.8.1).
    lines += ["", "", "def _initial(config):", "    s = _S()"]
    for (n, srt), v in zip(lps.params, lps.init):
        if roles.get(n) == "config":
            val = "config.%s" % ident(n)
        elif n in model.inits:
            val = "_m_gen_init_%s(%s)" % (ident(n), ", ".join("config.%s" % ident(c) for c in config))
        else:
            val = em.expr(v, {})
        r = width_range(model, n)
        if r is not None and n in fun_params(lps):
            lines.append("    s.%s = %s" % (ident(n), val))
            lines.append("    s.%s.r = (%d, %d, %r)" % (ident(n), r[0], r[1], n))
            continue
        if r is not None:
            val = "_fit(%s, %d, %d, %r)" % (val, r[0], r[1], n)
        lines.append("    s.%s = %s" % (ident(n), val))
    lines.append("    return s")
    extra = _stepper_slots(st) if st is not None else []
    lines += ["", "", "class _S:", "    __slots__ = (%s)" % "".join("%r, " % x for x in [ident(n) for n in names] + extra)]

    # Labels.
    inputs = sorted((a for a in model.actions if a.kind == "input"), key=lambda a: a.label)
    outputs = sorted((a for a in model.actions if a.kind == "output"), key=lambda a: a.label)
    lines.append("")
    for k, a in enumerate(inputs):
        lines.append("EV_%s = %d" % (a.label[2:].upper(), k))
    for k, a in enumerate(outputs):
        lines.append("ACT_%s = %d" % (a.label[2:].upper(), k))

    # The acceptor (§3.8.9): per summand a matcher and a firing.
    fps = fun_params(lps)
    by_code: dict = {}
    for k, s in enumerate(lps.summands):
        kind = model.summand_kind.get(s.name)
        labels = inputs if kind == "input" else outputs
        code = next(i for i, a in enumerate(labels) if a.label == s.label and len(a.sorts) == len(s.args))
        by_code.setdefault((0 if kind == "input" else 1, code), []).append(k)
        lines += [""] + _emit_summand(em, model, s, k, fps)
    lines += ["", "", "_ARGS = {"]
    for side, labels in ((0, inputs), (1, outputs)):
        for code, a in enumerate(labels):
            lines.append("    (%d, %d): %r," % (side, code, tuple(codec_desc(em.types, s) for s in a.sorts)))
    lines += ["}", "", "", "_SUMMANDS = {"]
    for key in sorted(by_code):
        lines.append("    %r: (%s),"
                     % (key, "".join("(_match%d, _fire%d), " % (k, k) for k in by_code[key])))
    lines.append("}")
    lines += ['''

class Acceptor:
    """Checks the action record against the unrefined Core (docs/spec.md §3.8.9)."""

    __slots__ = ("_s",)

    def __init__(self, config):
        self._s = _initial(config)

    def accept(self, entry):
        """entry: (0, event) or (1, action), each (code, arg1, ...)."""
        side, item = entry
        found = []
        for match, fire in _SUMMANDS.get((side, item[0]), ()):
            env = match(self._s, item[1:])
            if env is not None:
                found.append((fire, env))
        if not found:
            raise Rejected(entry)
        if len(found) > 1:
            raise Ambiguous(entry)
        found[0][0](self._s, found[0][1])

    def read(self, name):
        """A parameter's value (a stored function as a view)."""
        return getattr(self._s, name)
''']
    if st is not None:
        lines += _emit_stepper(em, model, st, inputs, outputs)
    return "\n".join(lines) + "\n"


def _emit_summand(em: Emitter, model, s, k, fps: set, watched: dict | None = None) -> list:
    lps = model.lps
    plan = plan_summand(em.types, s, fps, em.fun_maps)
    pscope = {n: "s.%s" % ident(n) for n, _ in lps.params}
    lines = ["", "def _match%s(s, a):" % k, "    # %s" % s.name]
    scope = dict(pscope)
    sv = {n: "v_" + ident(n) for n, _ in s.sumvars}
    for j, steps, c in plan.tags:
        acc = "a[%d]" % j + "".join(_step(cc, jj) for cc, jj in steps)
        lines.append("    if %s[0] != %d:" % (acc, c.index))
        lines.append("        return None")
    for b in plan.binds:
        acc = "a[%d]" % b.arg + "".join(_step(c, j) for c, j in b.steps)
        lines.append("    %s = %s" % (sv[b.var], acc))
        scope[b.var] = sv[b.var]
    for var, e in plan.fixed:
        lines.append("    %s = %s" % (sv[var], em.expr(e, scope)))
        scope[var] = sv[var]
    for j, a in enumerate(s.args):
        if isinstance(a, Var) and any(b.var == a.name and b.arg == j and not b.steps for b in plan.binds):
            continue
        lines.append("    if a[%d] != %s:" % (j, em.expr(a, scope)))
        lines.append("        return None")
    lines.append("    if not (%s):" % em.expr(s.cond, scope))
    lines.append("        return None")
    lines.append("    return (%s)" % "".join("%s, " % sv[n] for n, _ in s.sumvars))
    # The firing: every right side evaluated before anything is written (§3.8.4).
    lines += ["", "", "def _fire%s(s, env):" % k]
    if s.sumvars:
        lines.append("    %s = env" % "".join("%s, " % sv[n] for n, _ in s.sumvars))
    scope = dict(pscope)
    scope.update({n: sv[n] for n, _ in s.sumvars})
    lines += _fire_body(em, model, s, scope, fps, plan, watched)
    return lines


def _fire_body(em: Emitter, model, s, scope: dict, fps: set, plan, watched: dict | None) -> list:
    """The next state of a summand: every right side evaluated before anything is
    written (§3.8.4); a write to a set an index reads dirties the ids it changes
    (docs/spec.md §3.8.3; columns dirty themselves, `_Col.w`)."""
    lines = []
    writes = []
    plans = {}
    for n, e in s.assigns:
        if n in fps:
            pv = "_p_" + ident(n)
            lines.append("    %s = []" % pv)
            pl = []
            em.plan(e, n, scope, pl, "    ", pv, sorted_root=n in em.sorted.columns)
            lines.extend(pl)
            plans[n] = pv
        else:
            t = "_n_" + ident(n)
            lines.append("    %s = %s" % (t, em.expr(e, scope)))
            writes.append((n, t))
    for n in plan.fun_order:
        lines.append("    _run(s.%s, %s)" % (ident(n), plans[n]))
    for n, t in writes:
        r = width_range(model, n)
        if watched and n in watched:
            lines.append("    s._d_%s.extend(sorted(s.%s ^ %s))" % (watched[n], ident(n), t))
        lines.append("    s.%s = %s" % (ident(n), t if r is None else "_fit(%s, %d, %d, %r)" % (t, r[0], r[1], n)))
    if not s.assigns:
        lines.append("    pass")
    return lines


def _step(c, j) -> str:
    if c.shape == "id":
        return ""
    return "[%d]" % (j if c.shape == "record" else j + 1)


# ------------------------------------------------------------------ the stepper (docs/spec.md §3.8)

_ORDERS = {"least": 0, "key": 1, "newest": 2, "oldest": 3, "within": 4}


def _stepper_slots(st) -> list:
    sorts = sorted({ix.sort for ix in st.indexes} | set(st.watched.values()))
    return (["_d_%s" % x for x in sorts] + ["_ix_%s" % ix.name for ix in st.indexes]
            + ["_aud_%s" % ix.name for ix in st.indexes] + ["_audit"])


def _ix_fn(em: Emitter, model, ix, part: str, extra: str) -> list:
    """def _ix<part>_<name>(s[, v | k]) from the index's equation in the refined Core."""
    args, rhs = ix.eqs[part]
    scope = {}
    for a in args:
        if a.startswith("ix_p_"):
            scope[a] = "s.%s" % ident(a[len("ix_p_"):])
        else:
            scope[a] = extra
    head = "def _ix%s_%s(s%s):" % (part, ix.name, ", " + extra if extra else "")
    return ["", "", head, "    return %s" % em.expr(rhs, scope)]


def _emit_stepper(em: Emitter, model, st, inputs, outputs) -> list:
    m = st.model
    lps = m.lps
    fps = fun_params(lps)
    lines = []
    # The indexes' terms.
    for ix in st.indexes:
        lines += _ix_fn(em, model, ix, "mem", "v")
        if "cls" in ix.eqs:
            lines += _ix_fn(em, model, ix, "cls", "v")
            lines += _ix_fn(em, model, ix, "adm", "k")
        if "key" in ix.eqs:
            lines += _ix_fn(em, model, ix, "key", "v")
        lines += _ix_fn(em, model, ix, "bound", "")
        adm = "lambda k: _ixadm_%s(s, k)" % ix.name if "cls" in ix.eqs else "None"
        lines += ["", "", "def _ixfirst_%s(s):" % ix.name, "    return s._ix_%s.first(%s)" % (ix.name, adm),
                  "", "", "def _ixsome_%s(s):" % ix.name, "    return s._ix_%s.some(%s)" % (ix.name, adm),
                  "", "", "def _ixrefresh_%s(s, v):" % ix.name,
                  "    if _ixmem_%s(s, v):" % ix.name,
                  "        s._ix_%s.refresh(v, True, %s, %s)" % (
                      ix.name, "_ixcls_%s(s, v)" % ix.name if "cls" in ix.eqs else "0",
                      "s._ix_%s.ent[v][1]" % ix.within if ix.within else
                      "_ixkey_%s(s, v)" % ix.name if "key" in ix.eqs else "0"),
                  "    else:", "        s._ix_%s.refresh(v, False, 0, 0)" % ix.name]
    sorts = sorted({ix.sort for ix in st.indexes} | set(st.watched.values()))
    # The refresh after every summand (§3.8.3) and the audit (§3.9.9).
    lines += ["", "", "def _refresh(s):"]
    for srt in sorts:
        lines += ["    d = s._d_%s" % srt, "    if d:", "        ids = list(dict.fromkeys(d))", "        d.clear()",
                  "        for v in ids:"]
        mine = [ix for ix in st.indexes if ix.sort == srt]
        lines += ["            _ixrefresh_%s(s, v)" % ix.name for ix in mine] or ["            pass"]
    lines += ["    if s._audit:", "        _audit(s)"]
    lines += ["", "", "def _audit(s):", '    """Every index against its definition (docs/spec.md §3.9.9)."""']
    for ix in st.indexes:
        n = ix.name
        lines += ["    b = _ixbound_%s(s)" % n,
                  "    ref = [v for v in range(b) if _ixmem_%s(s, v)]" % n,
                  "    ent = s._ix_%s.ent" % n,
                  "    if set(ent) != set(ref):",
                  "        raise AuditFailed('index %s: members %%r, by definition %%r' %% (sorted(ent), ref))" % n]
        if "cls" in ix.eqs:
            lines += ["    for v in ref:", "        if ent[v][0] != _ixcls_%s(s, v):" % n,
                      "            raise AuditFailed('index %s: class of %%d' %% v)" % n]
        if "key" in ix.eqs:
            lines += ["    for v in ref:", "        if ent[v][1] != _ixkey_%s(s, v):" % n,
                      "            raise AuditFailed('index %s: key of %%d' %% v)" % n]
        if ix.order in ("newest", "oldest"):
            lines += ["    prev = s._aud_%s" % n, "    now = set(ref)",
                      "    entered = sorted(now - set(prev))",
                      "    if len(entered) > 1:",
                      "        raise AuditFailed('index %s: %%d ids entered in one summand' %% len(entered))" % n,
                      "    keep = [v for v in prev if v in now]",
                      "    s._aud_%s = %s" % (n, "entered + keep" if ix.order == "newest" else "keep + entered"),
                      "    if s._ix_%s.ordered() != s._aud_%s:" % (n, n),
                      "        raise AuditFailed('index %s: order %%r, by definition %%r' %% (s._ix_%s.ordered(), s._aud_%s))" % (n, n, n)]
        elif ix.within:
            # the order by definition: the list of the index it is within, filtered (§3.7.4 item 4)
            lines += ["    now = set(ref)", "    want = [v for v in s._aud_%s if v in now]" % ix.within,
                      "    if s._ix_%s.ordered() != want:" % n,
                      "        raise AuditFailed('index %s: order %%r, by definition %%r' %% (s._ix_%s.ordered(), want))" % (n, n)]
        else:
            okey = "_ixkey_%s(s, v), v" % n if "key" in ix.eqs else "v"
            lines += ["    if s._ix_%s.ordered() != sorted(ref, key=lambda v: (%s)):" % (n, okey),
                      "        raise AuditFailed('index %s: order')" % n]
    lines.append("    return None")
    # Input summands of the refined Core (§3.8.2) and output summands (§3.8.3).
    step_by_code: dict = {}
    for k in st.inputs:
        s = lps.summands[k]
        code = next(i for i, a in enumerate(inputs) if a.label == s.label and len(a.sorts) == len(s.args))
        step_by_code.setdefault(code, []).append(k)
        lines += [""] + _emit_summand(em, m, s, "_s%d" % k, fps, st.watched)
    pscope = {n: "s.%s" % ident(n) for n, _ in lps.params}
    outs = []
    for j, k in enumerate(st.outputs):
        s = lps.summands[k]
        code = next(i for i, a in enumerate(outputs) if a.label == s.label and len(a.sorts) == len(s.args))
        plan = plan_summand(em.types, s, fps, em.fun_maps)
        lines += ["", "", "def _out%d(s, out):" % j, "    # %s" % s.name,
                  "    if not (%s):" % em.expr(s.cond, pscope), "        return False"]
        lines.append("    out.append((%d,%s))" % (code, "".join(" %s," % em.expr(a, pscope) for a in s.args)))
        lines += _fire_body(em, m, s, dict(pscope), fps, plan, st.watched)
        lines.append("    return True")
        outs.append("_out%d" % j)
    lines += ["", "", "_OUTS = (%s)" % "".join("%s, " % o for o in outs), "", "_STEP = {"]
    for code in sorted(step_by_code):
        lines.append("    %d: (%s)," % (code, "".join("(_match_s%d, _fire_s%d), " % (k, k) for k in step_by_code[code])))
    lines.append("}")
    # The state's encoding, for the cross-check of the backends.
    lines += ["", "", "def _state_bytes(s):", "    out = bytearray()"]
    for n, srt in lps.params:
        lines += _enc_param(em, n, srt)
    lines.append("    return bytes(out)")
    init = ["    s._audit = audit", "    _AUDIT[0] = audit"]
    for srt in sorts:
        init.append("    s._d_%s = []" % srt)
    for p, srt in sorted(st.watched.items()):
        if p in fps:
            init.append("    s.%s.w = s._d_%s" % (ident(p), srt))
    for ix in st.indexes:
        init.append("    s._ix_%s = _Index(%d, %d)" % (ix.name, _ORDERS[ix.order], max(1, len(ix.classes))))
        init.append("    s._aud_%s = []" % ix.name)
    for ix in st.indexes:
        init += ["    for v in range(_ixbound_%s(s)):" % ix.name, "        _ixrefresh_%s(s, v)" % ix.name]
    lines += ["", "", "def _stepper_init(s, audit):"] + init + ["    if audit:", "        _audit(s)"]
    lines.append('''

def _loop(s, out):
    """The output loop (docs/spec.md §3.8.3): the first enabled output in
    policy order, until none is."""
    n = 0
    while True:
        for f in _OUTS:
            if f(s, out):
                break
        else:
            return
        _refresh(s)
        n += 1
        if n >= 1 << 24:
            raise StepLimit("2^24 actions in one step")


class State:
    """The stepper of the refined Core (docs/spec.md §3.8.1, §3.8.2, §3.12.2).
    With audit, every index is checked against its definition after every
    summand (§3.9.9): tests only."""

    __slots__ = ("_s",)

    def __init__(self, config, out, audit=False):
        s = _initial(config)
        _stepper_init(s, audit)
        self._s = s
        _loop(s, out)

    def step(self, event, out):
        s = self._s
        for match, fire in _STEP.get(event[0], ()):
            env = match(s, event[1:])
            if env is not None:
                fire(s, env)
                _refresh(s)
                _loop(s, out)
                return
        raise Refused(event)

    def bytes(self):
        """An estimate of the memory the state holds (§3.13.4)."""
        n = 0
        for name in _S.__slots__:
            v = getattr(self._s, name, None)
            if isinstance(v, _Col):
                n += 16 * len(v.m)
            elif isinstance(v, (tuple, frozenset, list)):
                n += 8 * len(v)
            else:
                n += 8
        return n

    def state_bytes(self):
        """Every parameter's value, encoded as entries are (for comparing the backends)."""
        return _state_bytes(self._s)

    def read(self, name):
        """A parameter's value (a stored function as a view)."""
        return getattr(self._s, name)''')
    return lines


def _enc_param(em: Emitter, n: str, srt) -> list:
    t = em.types
    s = t.resolve(srt)
    a = "s.%s" % ident(n)
    if s[0] == "->":
        kd, vd = codec_desc(t, s[1][0]), codec_desc(t, s[2])
        return ["    _enc(%s.d, %r, out)" % (a, vd), "    _enc(len(%s.m), ('n',), out)" % a,
                "    for k, v in sorted(%s.m.items()):" % a, "        _enc(k, %r, out)" % (kd,), "        _enc(v, %r, out)" % (vd,)]
    if s[0] == "FSet":
        return ["    _enc(sorted(%s), %r, out)" % (a, ("l", codec_desc(t, s[1])))]
    return ["    _enc(%s, %r, out)" % (a, codec_desc(t, s))]
