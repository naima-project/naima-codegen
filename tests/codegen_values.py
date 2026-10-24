"""Values of the model's sorts for the generator's backend tests: random values
from a seed, their mCRL2 text, their Python form in the generated Python
package (docs/spec.md §3.12), and a reader of the values mCRL2 prints.

Every walk over a value is a loop over an explicit stack.
"""

from __future__ import annotations

import random
import re

IDS = 4  # the ids of every id sort drawn from: 0 .. IDS - 1


class Gen:
    """Random values by sort, for a generated module `mod` (its _Col) and the model's Types."""

    def __init__(self, types, mod, seed: int):
        self.t = types
        self.mod = mod
        self.r = random.Random(seed)

    def value(self, s, depth: int = 0):
        """(mCRL2 text, Python value) of a random value of sort s."""
        t, r = self.t, self.r
        s = t.resolve(s)
        k = t.kind(s)
        if k == "bool":
            b = r.random() < 0.5
            return ("true" if b else "false"), b
        if k == "int":
            lo = 1 if s[1] == "Pos" else 0
            v = r.randint(lo, 3)
            # A numeral is typed Pos unless it is 0: cast it, or a lambda's body types it wrong.
            text = str(v) if s[1] == "Pos" or v == 0 else "Pos2Nat(%d)" % v
            return (text if s[1] != "Int" else "Nat2Int(%s)" % text), v
        if k in ("List", "FSet"):
            n = r.randint(0, 4 if depth == 0 else 2)
            items = [self.value(s[1], depth + 1) for _ in range(n)]
            if k == "List":
                return "[%s]" % ", ".join(a for a, _ in items), tuple(b for _, b in items)
            return ("{%s}" % ", ".join(a for a, _ in items) if items else "{}"), frozenset(b for _, b in items)
        if k == "fun":
            dom, cod = s[1][0], s[2]
            dtext, dval = self.value(cod, depth + 1)
            text, col = "(lambda x_: %s . %s)" % (sort_text(dom), dtext), self.mod._Col(dval)
            for _ in range(r.randint(0, 4)):
                (pt, pv), (vt, vv) = self.point(dom), self.value(cod, depth + 1)
                text += "[%s -> %s]" % (pt, vt)
                col.set(pv, vv)
            return text, col
        name = s[1]
        st = t.lps.structs[name]
        if t.shape[name] == "id":
            n = r.randint(0, IDS - 1)
            return "%s(%d)" % (st.constructors[0][0], n), n
        k2 = r.randrange(len(st.constructors))
        c, fields, _ = st.constructors[k2]
        if not fields:
            return c, (k2 if t.shape[name] == "enumeration" else (k2,))
        vals = [self.value(fs, depth + 1) for _, fs in fields]
        text = "%s(%s)" % (c, ", ".join(a for a, _ in vals))
        py = tuple(b for _, b in vals)
        return text, (py if t.shape[name] == "record" else (k2,) + py)

    def point(self, s):
        return self.value(s, 9)

    def points(self, s):
        """Every point of a small domain: (mCRL2 text, Python value)."""
        t = self.t
        s = t.resolve(s)
        name = s[1]
        st = t.lps.structs[name]
        if t.shape[name] == "id":
            n = IDS
            return [("%s(%d)" % (st.constructors[0][0], k), k) for k in range(n + 1)]
        if t.shape[name] == "enumeration":
            return [(c, k) for k, (c, _, _) in enumerate(st.constructors)]
        # a record of id sorts, such as GKey
        out = [((), ())]
        for _, fs in st.constructors[0][1]:
            out = [(a + (x,), b + (y,)) for a, b in out for x, y in self.points(fs)]
        c = st.constructors[0][0]
        return [("%s(%s)" % (c, ", ".join(a)), b) for a, b in out]


def sort_text(s) -> str:
    out, stack = [], [(s, False)]
    while stack:
        x, seen = stack.pop()
        if x[0] == "id":
            out.append(x[1])
        elif x[0] == "->":
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
                out.append("(%s -> %s)" % (" # ".join(ds), cod))
        else:
            if not seen:
                stack.append((x, True))
                stack.append((x[1], False))
            else:
                out.append("%s(%s)" % (x[0], out.pop()))
    return out[0]


_TOKEN = re.compile(r"\s*(?:(-?\d+)|([A-Za-z_][A-Za-z0-9_']*)|(.))")


def parse_printed(text: str):
    """The tree of a value as mCRL2 prints it: ("num", n), ("app", name, args),
    ("list", items), ("set", items)."""
    toks = []
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
        if m.group(1):
            toks.append(("num", int(m.group(1))))
        elif m.group(2):
            toks.append(("id", m.group(2)))
        elif m.group(3) and m.group(3).strip():
            toks.append(("p", m.group(3)))
    # Shift-reduce with an explicit stack of open brackets.
    stack = [["top", None, []]]
    k = 0
    while k < len(toks):
        kind, v = toks[k]
        if kind == "num":
            stack[-1][2].append(("num", v))
        elif kind == "id":
            if k + 1 < len(toks) and toks[k + 1] == ("p", "("):
                stack.append(["app", v, []])
                k += 1
            elif v in ("true", "false"):
                stack[-1][2].append(("bool", v == "true"))
            else:
                stack[-1][2].append(("app", v, []))
        elif v == "[":
            stack.append(["list", None, []])
        elif v == "{":
            stack.append(["set", None, []])
        elif v in ")]}":
            kind2, name, items = stack.pop()
            stack[-1][2].append(("app", name, items) if kind2 == "app" else (kind2, items))
        k += 1
    return stack[0][2][0]


def to_python(tree, s, types):
    """The Python form of a printed value of sort s (a loop over an explicit stack)."""
    out: list = []
    stack = [(tree, s, False)]
    while stack:
        x, srt, seen = stack.pop()
        srt = types.resolve(srt)
        k = types.kind(srt)
        if k in ("bool", "int"):
            out.append(x[1])
            continue
        if k in ("List", "FSet"):
            items = x[1]
            if not seen:
                stack.append((x, srt, True))
                for it in reversed(items):
                    stack.append((it, srt[1], False))
                continue
            vals = out[len(out) - len(items):] if items else []
            del out[len(out) - len(items):]
            out.append(tuple(vals) if k == "List" else frozenset(vals))
            continue
        name = srt[1]
        shape = types.shape[name]
        c = types.cons[x[1]]
        if not c.fields:
            out.append(c.index if shape == "enumeration" else (c.index,))
            continue
        if not seen:
            stack.append((x, srt, True))
            for it, (_, fs) in reversed(list(zip(x[2], c.fields))):
                stack.append((it, fs, False))
            continue
        n = len(c.fields)
        vals = out[len(out) - n:]
        del out[len(out) - n:]
        if shape == "id":
            out.append(vals[0])
        elif shape == "record":
            out.append(tuple(vals))
        else:
            out.append((c.index,) + tuple(vals))
    return out[0]


def unbind_ids(label: str, model, ids_conf: dict) -> str:
    """A transition label of a generated system with the bounded ids' constants
    (J3) written back as the model writes them (job(3)), by [systems.ids] (§3.17)."""
    from naima_codegen.systems import IdBounds

    for sort, prefix, _, _ in IdBounds(model.config.id_sorts, ids_conf).sorts:
        ctor = model.lps.structs[sort].constructors[0][0]
        label = re.sub(r"\b%s(\d+)\b" % re.escape(prefix), ctor + r"(\1)", label)
    return label
