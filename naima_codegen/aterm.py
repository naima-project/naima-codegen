"""A reader of the textual ATerm that `lpspp --format=internal` prints
(docs/spec.md §3.2.2).

The text is a tree of applications `f(t1, ..., tn)`, lists `[t1, ..., tn]` and
integers. A function name is either a run of characters other than
`(),[]"` and white space, or a double-quoted string (`"[]"`, `"-"`). An empty
argument, as in `StructCons(a,[],)`, is the application of the empty name.

The reader is a loop over an explicit stack: the nesting of a term is as deep
as the model's expressions, and nothing here recurses.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, eq=False)
class Appl:
    """An application `name(args)`; a constant has no arguments."""

    name: str
    args: tuple

    def __repr__(self) -> str:
        return f"Appl({self.name!r}, {len(self.args)} args)"


class ATermError(ValueError):
    """The text is not a well-formed ATerm."""


_DELIM = set('(),[]" \t\r\n')


def parse(text: str):
    """The term the text denotes: Appl, tuple (a list) or int."""
    n = len(text)
    i = 0
    # Each frame: [kind, name, items]; kind "(" for an application, "[" for a list.
    stack: list[list] = []
    result = None
    done = False

    def skip(i: int) -> int:
        while i < n and text[i] in " \t\r\n":
            i += 1
        return i

    while True:
        i = skip(i)
        if i >= n:
            raise ATermError("unexpected end of text")
        c = text[i]
        # Read one item (or open a frame).
        if c == "[":
            stack.append(["[", None, []])
            i = skip(i + 1)
            if i < n and text[i] == "]":
                i += 1
                stack.pop()
                value = ()
            else:
                continue
        else:
            if c == '"':
                j = i + 1
                buf = []
                while j < n and text[j] != '"':
                    if text[j] == "\\" and j + 1 < n:
                        j += 1
                    buf.append(text[j])
                    j += 1
                if j >= n:
                    raise ATermError("unterminated string at %d" % i)
                name = "".join(buf)
                i = j + 1
                quoted = True
            else:
                j = i
                while j < n and text[j] not in _DELIM:
                    j += 1
                name = text[i:j]
                i = j
                quoted = False
            i = skip(i)
            if i < n and text[i] == "(":
                stack.append(["(", name, []])
                i = skip(i + 1)
                if i < n and text[i] == ")":
                    i += 1
                    stack.pop()
                    value = Appl(name, ())
                else:
                    continue
            elif not quoted and name and (name.isdigit() or (name[0] == "-" and name[1:].isdigit())):
                value = int(name)
            else:
                value = Appl(name, ())
        # Attach the value; close frames that end here.
        while True:
            if not stack:
                result = value
                done = True
                break
            frame = stack[-1]
            frame[2].append(value)
            i = skip(i)
            if i >= n:
                raise ATermError("unexpected end of text")
            close = ")" if frame[0] == "(" else "]"
            if text[i] == ",":
                i += 1
                # An empty argument: `f(a,)` or `f(a,,b)`.
                k = skip(i)
                if frame[0] == "(" and k < n and text[k] in ",)":
                    value = Appl("", ())
                    i = k
                    continue
                break
            if text[i] == close:
                i += 1
                stack.pop()
                value = Appl(frame[1], tuple(frame[2])) if frame[0] == "(" else tuple(frame[2])
                continue
            raise ATermError("expected ',' or %r at %d, found %r" % (close, i, text[i]))
        if done:
            break
    if skip(i) != n:
        raise ATermError("text after the term at %d" % i)
    return result


def _name_text(name: str) -> str:
    if name and (any(c in _DELIM for c in name) or name.isdigit() or name.startswith("-")):
        return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return name


def to_text(term) -> str:
    """The term as ATerm text (the form `lpspp --format=internal` prints).

    Used to order summands by the text of their condition (docs/spec.md
    §3.5.4)."""
    out: list[str] = []
    # Items: a term to print, or a literal string to emit.
    stack: list = [term]
    while stack:
        t = stack.pop()
        if isinstance(t, str):
            out.append(t)
        elif isinstance(t, int):
            out.append(str(t))
        elif isinstance(t, tuple):
            stack.append("]")
            for k in range(len(t) - 1, -1, -1):
                stack.append(t[k])
                if k:
                    stack.append(",")
            out.append("[")
        else:
            nm = _name_text(t.name)
            if t.args:
                stack.append(")")
                for k in range(len(t.args) - 1, -1, -1):
                    stack.append(t.args[k])
                    if k:
                        stack.append(",")
                out.append(nm + "(")
            else:
                out.append(nm)
    return "".join(out)


def rebuild(term, fn):
    """term with fn applied bottom-up to every application (lists are rebuilt,
    integers kept): fn receives an Appl with rebuilt arguments."""
    out: list = []
    stack: list = [(term, False)]
    while stack:
        t, seen = stack.pop()
        if isinstance(t, int):
            out.append(t)
            continue
        kids = t if isinstance(t, tuple) else t.args
        if not kids:
            out.append(t if isinstance(t, tuple) else fn(t))
            continue
        if not seen:
            stack.append((t, True))
            for k in reversed(kids):
                stack.append((k, False))
            continue
        n = len(kids)
        new = tuple(out[len(out) - n:])
        del out[len(out) - n:]
        out.append(new if isinstance(t, tuple) else fn(Appl(t.name, new)))
    return out[0]
