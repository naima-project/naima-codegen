"""What the generator reads from mCRL2 source text, besides the LPS: the
declarations of `model/core.mcrl2` that linearisation does not keep (which
processes there are, how Core is written, the sum variables it names), and the
catalogue entries' reference text (docs/spec.md §3.3.6, §3.5.1, §3.10.1).

This is a scanner, not a parser: mCRL2 parses the model (§3.2.2); the scanner
only finds declarations and forbidden constructs in text mCRL2 accepted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

KEYWORDS = ("sort", "cons", "map", "var", "eqn", "act", "proc", "init", "glob")
_SECTION = re.compile(r"\b(" + "|".join(KEYWORDS) + r")\b")


def strip_comments(text: str) -> str:
    """text with every `%` comment blanked (lengths and lines kept)."""
    return re.sub(r"%[^\n]*", lambda m: " " * len(m.group(0)), text)


def split_top(text: str, sep: str) -> list[str]:
    """text split at sep where no bracket is open."""
    out, depth, cur, i = [], 0, [], 0
    while i < len(text):
        c = text[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if depth == 0 and text.startswith(sep, i):
            out.append("".join(cur))
            cur = []
            i += len(sep)
            continue
        cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


def sections(text: str) -> list[tuple[str, str, int]]:
    """The (keyword, body, offset) sections of an mCRL2 text without comments."""
    t = strip_comments(text)
    marks = [(m.start(), m.group(1)) for m in _SECTION.finditer(t)]
    out = []
    for k, (pos, kw) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(t)
        out.append((kw, t[pos + len(kw):end], pos + len(kw)))
    return out


def declared(text: str) -> dict[str, list[str]]:
    """The names declared per section: sorts, constructors and maps, actions, processes."""
    out: dict[str, list[str]] = {k: [] for k in KEYWORDS}
    for kw, body, _ in sections(text):
        for decl in split_top(body, ";"):
            d = decl.strip()
            if not d:
                continue
            if kw == "sort":
                m = re.match(r"([A-Za-z_][\w']*)", d)
                if m:
                    out[kw].append(m.group(1))
            elif kw in ("cons", "map", "act", "var", "glob"):
                names = split_top(d, ":")[0] if ":" in d else d
                for n in names.split(","):
                    n = n.strip()
                    if re.fullmatch(r"[A-Za-z_][\w']*", n):
                        out[kw].append(n)
            elif kw == "proc":
                m = re.match(r"([A-Za-z_][\w']*)", d)
                if m:
                    out[kw].append(m.group(1))
    return out


@dataclass
class CoreSource:
    """The facts of model/core.mcrl2 the LPS does not carry."""

    text: str
    decls: dict
    core_body: str  # Core's right-hand side, comments blanked ("" when Core is missing)
    sumvars: set  # sum variable names written in Core
    params: list = field(default_factory=list)  # (name, sort text) of Core's parameters, as written
    labels: list = field(default_factory=list)  # the label of each summand, in source order (§3.5.4)


def read_core(text: str) -> CoreSource:
    decls = declared(text)
    body = ""
    params = []
    for kw, sec, _ in sections(text):
        if kw != "proc":
            continue
        for decl in split_top(sec, ";"):
            m = re.match(r"\s*Core\b", decl)
            if m:
                eq = split_top(decl, "=")
                body = "=".join(eq[1:]) if len(eq) > 1 else ""
                head = eq[0][m.end():].strip()
                if head.startswith("(") and head.endswith(")"):
                    for d in split_top(head[1:-1], ","):
                        if ":" in d:
                            n, s = d.split(":", 1)
                            params.append((n.strip(), " ".join(s.split())))
    sumvars = set()
    for m in re.finditer(r"\bsum\b(.*?)\.", body, re.S):
        for d in split_top(m.group(1), ","):
            n = d.split(":")[0].strip()
            if n:
                sumvars.add(n)
    labels = re.findall(r"->\s*(c_[A-Za-z0-9_']*)\s*[(.]", body)
    return CoreSource(text, decls, body, sumvars, params, labels)


# Constructs refused in Core (§3.5.1): process operators that a linear Core never uses.
_FORBIDDEN = [
    (re.compile(r"\ballow\s*\("), "allow"),
    (re.compile(r"\bcomm\s*\("), "comm"),
    (re.compile(r"\bhide\s*\("), "hide"),
    (re.compile(r"\brename\s*\("), "rename"),
    (re.compile(r"\bblock\s*\("), "block"),
    (re.compile(r"\btau\b"), "tau"),
    (re.compile(r"@"), "time (@)"),
    (re.compile(r"<>"), "the c -> p <> q form"),
    (re.compile(r"\|\|_"), "left merge"),
]


def forbidden_in_core(src: CoreSource) -> list[str]:
    """The forbidden constructs written in Core's body, and calls of other processes."""
    found = [what for rx, what in _FORBIDDEN if rx.search(src.core_body)]
    for p in src.decls["proc"]:
        if p != "Core" and re.search(r"\b" + re.escape(p) + r"\b", src.core_body):
            found.append("a call of process %s" % p)
    return found


@dataclass
class Entry:
    """A catalogue entry (§3.10.1): its reference text and the names it declares."""

    name: str
    text: str | None  # reference.mcrl2, None for entries over built-in sorts
    sorts: list = field(default_factory=list)
    maps: list = field(default_factory=list)


def read_catalogue(directory: Path) -> list[Entry]:
    """The entries under codegen/catalogue/ (none when the directory is absent)."""
    out = []
    if not directory.is_dir():
        return out
    for d in sorted(p for p in directory.iterdir() if p.is_dir()):
        ref = d / "reference.mcrl2"
        if ref.is_file():
            text = ref.read_text(encoding="utf-8")
            decls = declared(text)
            out.append(Entry(d.name, text, decls["sort"], decls["cons"] + decls["map"]))
        else:
            out.append(Entry(d.name, None))
    return out
