"""A small evaluator of closed data expressions of the LPS, for the check of
`init` (docs/spec.md §3.6.2): an expression over configuration
parameters, evaluated on the placeholder configuration of
`model/codegen-init.mcrl2`, must equal the placeholder value there.

It covers what initial values are written with: numerals, Booleans,
constructors, projections and recognisers, the arithmetic and comparisons of
§3.4.1, `if`, empty collections, constant lambdas, and non-recursive user maps
by their equations. Anything else raises `NotEvaluable`. One explicit stack, no
recursion.
"""

from __future__ import annotations

from .expr import App, Bind, Num, Op, Var, Whr


class NotEvaluable(Exception):
    pass


_ARITH = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "div": lambda a, b: a // b,
    "mod": lambda a, b: a % b,
    "min": min,
    "max": max,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "&&": lambda a, b: a and b,
    "||": lambda a, b: a or b,
    "=>": lambda a, b: (not a) or b,
}
_UNARY = {
    "!": lambda a: not a,
    "-": lambda a: -a,
    "succ": lambda a: a + 1,
    "pred": lambda a: a - 1,
    "abs": abs,
    "Int2Nat": lambda a: a,
    "Nat2Pos": lambda a: a,
    "Pos2Nat": lambda a: a,
    "Nat2Int": lambda a: a,
    "Pos2Int": lambda a: a,
    "#": len,
}


def evaluate(e, env: dict, lps) -> object:
    """The value of e with variables bound by env (name -> value)."""
    cons, proj, recog = {}, {}, {}
    for st in lps.structs.values():
        for c, fields, r in st.constructors:
            cons[c] = len(fields)
            for k, (p, _) in enumerate(fields):
                if p:
                    proj[p] = (c, k)
            if r:
                recog[r] = c
    eqs = {}
    for q in lps.equations:
        h = q.lhs.head if isinstance(q.lhs, App) else q.lhs
        if isinstance(h, Op):
            eqs.setdefault(h.name, []).append(q)

    budget = 100_000
    out: list = []
    stack = [(e, env, False)]
    while stack:
        budget -= 1
        if budget < 0:
            raise NotEvaluable("too long")
        x, en, seen = stack.pop()
        if isinstance(x, Num):
            out.append(x.value)
        elif isinstance(x, Var):
            if x.name not in en:
                raise NotEvaluable("free variable %s" % x.name)
            out.append(en[x.name])
        elif isinstance(x, Op):
            n = x.name
            if n in ("true", "false"):
                out.append(n == "true")
            elif n in ("[]",):
                out.append(("list", ()))
            elif n in ("{}", "@fset_empty", "@fbag_empty"):
                out.append(("set", frozenset()))
            elif n in cons and cons[n] == 0:
                out.append(("c", n, ()))
            elif n in eqs:
                q = [q for q in eqs[n] if not isinstance(q.lhs, App)]
                if not q:
                    raise NotEvaluable("map %s" % n)
                stack.append((q[0].rhs, {}, False))
            else:
                raise NotEvaluable("operator %s" % n)
        elif isinstance(x, Bind):
            if x.kind != "Lambda":
                raise NotEvaluable(x.kind)
            stack.append((("@lambda",), en, False))
            stack.append((x.body, en, False))
        elif isinstance(x, tuple) and x == ("@lambda",):
            out.append(("fun", out.pop()))
        elif isinstance(x, Whr):
            if not seen:
                stack.append((x, en, True))
                for _, d in x.defs:
                    stack.append((d, en, False))
            else:
                vals = [out.pop() for _ in x.defs][::-1]
                en2 = dict(en)
                for (v, _), val in zip(x.defs, vals):
                    en2[v.name] = val
                stack.append((x.body, en2, False))
        elif isinstance(x, App):
            h = x.head
            hn = h.name if isinstance(h, Op) else None
            if hn == "if" and len(x.args) == 3:
                if not seen:
                    stack.append((x, en, True))
                    stack.append((x.args[0], en, False))
                else:
                    c = out.pop()
                    stack.append((x.args[1] if c else x.args[2], en, False))
                continue
            if hn in eqs and hn not in cons:
                if not seen:
                    stack.append((x, en, True))
                    for a in reversed(x.args):
                        stack.append((a, en, False))
                else:
                    args = [out.pop() for _ in x.args][::-1]
                    stack.append(_unfold(eqs[hn], args, hn))
                continue
            if not seen:
                stack.append((x, en, True))
                for a in reversed(x.args):
                    stack.append((a, en, False))
                if not isinstance(h, Op):
                    stack.append((h, en, False))
                continue
            args = [out.pop() for _ in x.args][::-1]
            if not isinstance(h, Op):
                out.append(_apply_value(out.pop(), args))
            elif hn in cons:
                out.append(("c", hn, tuple(args)))
            elif hn in proj:
                c, k = proj[hn]
                v = args[0]
                if not (isinstance(v, tuple) and v[0] == "c" and v[1] == c):
                    raise NotEvaluable("projection %s of %r" % (hn, v))
                out.append(v[2][k])
            elif hn in recog:
                out.append(isinstance(args[0], tuple) and args[0][1] == recog[hn])
            elif hn in _ARITH and len(args) == 2:
                out.append(_ARITH[hn](args[0], args[1]))
            elif hn in _UNARY and len(args) == 1:
                out.append(_UNARY[hn](args[0]))
            elif hn in ("@cNat", "@cInt") and len(args) == 1:
                out.append(args[0])
            elif hn == "@cNeg" and len(args) == 1:
                out.append(-args[0])
            else:
                raise NotEvaluable("operator %s" % hn)
        else:
            raise NotEvaluable(repr(x))
    return out[-1]


def _apply_value(f, args):
    if isinstance(f, tuple) and f[0] == "fun":
        return f[1]
    raise NotEvaluable("application of %r" % (f,))


def _unfold(eqs, args, name):
    """The right side of the equation of `name` that applies to args, with its variables bound."""
    for q in eqs:
        if not (isinstance(q.cond, Op) and q.cond.name == "true"):
            raise NotEvaluable("conditional equation of %s" % name)
        env = {}
        ok = True
        for p, v in zip(q.lhs.args, args):
            if isinstance(p, Var):
                env[p.name] = v
            elif isinstance(p, Op):
                if not (isinstance(v, tuple) and v[0] == "c" and v[1] == p.name):
                    ok = False
                    break
            elif isinstance(p, App) and isinstance(p.head, Op) and all(isinstance(a, Var) for a in p.args):
                if not (isinstance(v, tuple) and v[0] == "c" and v[1] == p.head.name):
                    ok = False
                    break
                for a, fv in zip(p.args, v[2]):
                    env[a.name] = fv
            else:
                raise NotEvaluable("pattern of %s" % name)
        if ok:
            return (q.rhs, env, False)
    raise NotEvaluable("no equation of %s applies" % name)
