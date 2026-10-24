"""The refined Core (docs/spec.md §3.7.2, §3.7.4) and the probe Core (§3.9.3,
§3.9.6, §3.9.7), written as mCRL2 text from `model/core.mcrl2` and the policy.

The source summands are read as text (mCRL2 parses them when the result is
linearised). Each output summand gets its selectors substituted for its free sum
variables, then its equality-fixed variables substituted (§3.7.2.1); each input
summand gets its equality-fixed variables that are not action arguments
substituted (§3.7.2.4). The selection operations become the generator's reference
definitions, maps named `ix_*` (§3.7.4 item 2); an index of order newest or
oldest becomes a parameter `ix_<name>: List(I)` kept by every summand that writes
what its membership reads (§3.7.4 item 3).

Three Cores are written from the same summands:

- `refined`: the refined Core of §3.7.4, with the priority conjuncts of §3.7.2.2
  and the quiescence conjunct `Q` of §3.7.2.3, for the checks of §3.9;
- `stepper`: the same without the priority and `Q` conjuncts, which the output
  loop of §3.8.3 makes true by trying the summands in order; the stepper is
  generated from its LPS (§3.7.5);
- `probe`: `refined` plus the probe summands of §3.9.3, §3.9.6 and §3.9.7.

Every pass over text is a loop over positions; nothing recurses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .errors import Diagnostic, GeneratorError
from .policy import POLICY, Policy, idents, operations
from .source import split_top, strip_comments
from .header import block as _header_block

CORE = "model/core.mcrl2"
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_']*")
_TAG = re.compile(r"^%@(order \d+|input)[ \t]*$", re.M)


@dataclass
class Src:
    """One summand of Core as the source writes it (comments blanked)."""

    kind: str  # input or output
    level: int | None
    label: str
    name: str
    decls: list  # (name, sort text)
    cond: str
    action: str  # c_x(args) or c_x
    assigns: list  # (parameter, right side text), in source order


@dataclass
class CoreText:
    head: str  # everything before `proc Core(`
    params: list  # (name, sort text)
    summands: list  # Src
    tail: str  # from the end of Core's body (`%@observers`, `+ delta;`) to the end
    ids: dict = field(default_factory=dict)  # id sort -> constructor
    acts: dict = field(default_factory=dict)  # label -> [sort texts], per arity


# ------------------------------------------------------------------ text helpers


def matching(text: str, i: int) -> int:
    """The index just after the bracket that closes the one at text[i]."""
    depth = 0
    for k in range(i, len(text)):
        c = text[k]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return k + 1
    raise ValueError("unbalanced brackets")


def _depths(text: str) -> list:
    out, d = [], 0
    for c in text:
        if c in ")]}":
            d -= 1
        out.append(d)
        if c in "([{":
            d += 1
    return out


def _binder_regions(text: str) -> list:
    """(start, end, names) of every exists/forall/lambda in text: the names it binds
    and the extent of its body (to the end of the enclosing bracket group)."""
    out = []
    depth = _depths(text)
    for m in re.finditer(r"\b(exists|forall|lambda)\b", text):
        k = m.end()
        dot = text.find(".", k)
        if dot < 0:
            continue
        names = []
        for part in text[k:dot].split(","):
            n = part.split(":")[0].strip()
            if n:
                names.append(n)
        d0, end = depth[m.start()], len(text)
        for j in range(m.start(), len(text)):
            if depth[j] < d0 or (depth[j] == d0 and text[j] in ")]}"):
                end = j
                break
        out.append((m.start(), end, set(names)))
    return out


def subst(text: str, mapping: dict) -> str:
    """text with every free occurrence of the variables of mapping replaced by
    `(value)`, simultaneously; occurrences bound by a binder are kept."""
    if not mapping:
        return text
    regions = _binder_regions(text)
    out, last = [], 0
    for m in _IDENT.finditer(text):
        x = m.group(0)
        if x not in mapping:
            continue
        if any(a <= m.start() < b and x in names for a, b, names in regions):
            continue
        out.append(text[last:m.start()])
        out.append("(" + mapping[x] + ")")
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def conjuncts(text: str) -> list:
    """The top-level conjuncts of a condition, nested parenthesised conjunctions flattened."""
    out, stack = [], [text]
    while stack:
        t = stack.pop().strip()
        while t.startswith("(") and matching(t, 0) == len(t):
            t = t[1:-1].strip()
        parts = split_top(t, "&&")
        if len(parts) == 1:
            out.append(t)
            continue
        stack.extend(reversed(parts))
    return out


def norm(text: str) -> str:
    return " ".join(text.split())


# ------------------------------------------------------------------ reading Core


def read_core_text(text: str) -> CoreText:
    t = strip_comments(text)
    start = t.index("proc Core(")
    pclose = matching(t, start + len("proc Core"))
    params = []
    pending = []
    for d in split_top(t[start + len("proc Core("):pclose - 1], ","):
        if ":" in d:
            n, s = d.split(":", 1)
            for x in pending + [n]:
                params.append((x.strip(), norm(s)))
            pending = []
        elif d.strip():
            pending.append(d)
    eq = t.index("=", pclose)
    body_start = eq + 1
    obs = t.find("%@observers", body_start)
    # strip_comments blanks %@ tags too: find them in the original text (same offsets).
    obs = text.find("%@observers", body_start)
    if obs < 0:
        last = t.rstrip().rstrip(";")
        k = last.rfind("+ delta")
        obs = k if k > 0 else len(last)
    body_orig = text[body_start:obs]
    tags = list(_TAG.finditer(body_orig))
    summands = []
    for i, m in enumerate(tags):
        chunk = strip_comments(body_orig[m.end():tags[i + 1].start() if i + 1 < len(tags) else len(body_orig)])
        tag = m.group(1)
        summands.append(_parse_summand(chunk, tag))
    counts: dict = {}
    for s in summands:
        k = counts[s.label] = counts.get(s.label, 0) + 1
        s.name = s.label if k == 1 else "%s#%d" % (s.label, k)
    ids = {m.group(1): m.group(2) for m in re.finditer(
        r"\bsort\s+([A-Za-z_]\w*)\s*=\s*struct\s+([A-Za-z_]\w*)\s*\(\s*\w+\s*:\s*Nat\s*\)\s*;", t)}
    acts: dict = {}
    for m in re.finditer(r"\bact\b(.*?)(?=\b(?:sort|cons|map|var|eqn|proc|init|glob|act)\b|$)", t, re.S):
        for decl in m.group(1).split(";"):
            if not decl.strip():
                continue
            names, _, sorts = decl.partition(":")
            ss = [norm(x) for x in split_top(sorts, "#")] if sorts.strip() else []
            for n in names.split(","):
                if n.strip():
                    acts.setdefault(n.strip(), []).append(ss)
    return CoreText(text[:start], params, summands, text[obs:], ids, acts)


def _parse_summand(chunk: str, tag: str) -> Src:
    c = chunk.strip()
    if c.startswith("+"):
        c = c[1:].strip()
    decls = []
    m = re.match(r"sum\b", c)
    if m:
        dot = c.index(".", m.end())
        pending = []
        for d in split_top(c[m.end():dot], ","):
            if ":" in d:
                n, s = d.split(":", 1)
                for x in pending + [n]:
                    decls.append((x.strip(), norm(s)))
                pending = []
            elif d.strip():
                pending.append(d.strip())
        c = c[dot + 1:].strip()
    arrow = _top_index(c, "->")
    cond = c[:arrow].strip()
    rest = c[arrow + 2:].strip()
    core = _top_index(rest, "Core")
    act = rest[:core].strip()
    if act.endswith("."):
        act = act[:-1].strip()
    k = rest.index("(", core)
    inner = rest[k + 1:matching(rest, k) - 1]
    assigns = []
    for a in split_top(inner, ","):
        if not a.strip():
            continue
        mm = re.match(r"\s*([A-Za-z_][\w']*)\s*=(?!=)", a)
        assigns.append((mm.group(1), a[mm.end():].strip()))
    label = re.match(r"(c_[\w']*)", act).group(1)
    kind = "input" if tag == "input" else "output"
    level = None if kind == "input" else int(tag.split()[1])
    return Src(kind, level, label, "", decls, cond, act, assigns)


def _top_index(text: str, token: str) -> int:
    depth = 0
    for i, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and text.startswith(token, i):
            if token[0].isalpha() and i > 0 and (text[i - 1].isalnum() or text[i - 1] == "_"):
                continue
            return i
    raise ValueError("no top-level %s in %r" % (token, text[:80]))


# ------------------------------------------------------------------ the construction


@dataclass
class Refined:
    """The texts of §3.7.4 and §3.9, and what the backends need of them."""

    refined: str
    stepper: str
    probe: str
    probe_mcf: str
    quiescent: str  # Q of §3.7.2.3 over the parameters
    inputs: list  # Src, source order (refined: selectors and equalities substituted)
    outputs: list  # Src, policy order
    list_params: list  # (name, sort text) of the ix_ parameters
    init_suffix: str  # ", [], ..." appended to an init of Core
    indexes: dict  # name -> IndexInfo
    deferred: list  # summand names
    out_labels: list  # vocabulary names of the output labels
    synced: list = field(default_factory=list)  # (vocabulary label, marker, sorts): input probes taken with the event


@dataclass
class IndexInfo:
    name: str
    sort: str
    ctor: str
    order: str
    mem_args: list  # parameters the membership reads
    cls_args: list
    key_args: list
    adm_args: list
    bound_args: list
    cls_sort: str | None
    key_sort: str | None
    list_param: str | None  # ix_<name> for newest and oldest


def refine(core_text: str, policy: Policy, kinds: dict | None = None) -> Refined:
    """The refined, stepper and probe Cores; GeneratorError (G9, G10, G13) on what
    the policy or the model does not allow."""
    ct = read_core_text(core_text)
    problems: list = []
    params = dict(ct.params)
    columns = {n for n, s in ct.params if "->" in s and s.split("->")[0].strip() in ct.ids}
    sets = {n for n, s in ct.params if re.fullmatch(r"FSet\((\w+)\)", s) and re.fullmatch(r"FSet\((\w+)\)", s).group(1) in ct.ids}

    def g(code, where, section, msg):
        problems.append(Diagnostic(code, POLICY if code == "G9" else CORE, where, section, msg))

    indexes = _index_infos(policy, ct, params, g)
    by_name = {s.name: s for s in ct.summands}
    outputs = [by_name[n] for n in policy.order if n in by_name]
    inputs = [s for s in ct.summands if s.kind == "input"]
    if kinds:
        for s in ct.summands:
            if kinds.get(s.name) not in (None, s.kind):
                g("G10", "summand %s" % s.name, "§3.5.4", "the source says %s, the declaration %s" % (s.kind, kinds.get(s.name)))

    least_sorts: set = set()

    def ops(t: str) -> str:
        def rep(m):
            op, a = m.group(1), m.group(2)
            if op == "least":
                el = re.fullmatch(r"FSet\((\w+)\)", params.get(a, "")).group(1)
                least_sorts.add(el)
                return "ix_least_%s(%s, 0)" % (el, a)
            ix = indexes[a]
            q = _all_args(ix)
            if ix.list_param:
                return "ix_%s_%s(%s)" % (op, a, ", ".join([ix.list_param] + q))
            if op == "some":
                return "ix_some_%s(%s)" % (a, ", ".join(q + ["0"]))
            if ix.order == "key":
                return "ix_first_%s(%s)" % (a, ", ".join(q + ["0", "%s(0)" % ix.ctor, "false"]))
            return "ix_first_%s(%s)" % (a, ", ".join(q + ["0"]))

        return re.sub(r"\b(least|first|some)\s*\(\s*([A-Za-z_][A-Za-z0-9_']*)\s*\)", rep, t)

    refined_out = []
    for s in outputs:
        r = _refine_output(s, policy, ops, g)
        refined_out.append(r)
    refined_in = [_refine_input(s, g) for s in inputs]
    for r in refined_in + refined_out:
        _upkeep(r, indexes, params, columns, sets, g)
    if problems:
        raise GeneratorError(problems)

    # The enabledness of each refined output, and Q, as maps over the parameters
    # they read: the priority conjuncts of §3.7.2.2 and §3.7.2.3 then cost one
    # call each in the text instead of a copy of the condition.
    lps_ = [(ix.list_param, "List(%s)" % ix.sort) for ix in indexes.values() if ix.list_param]
    order = [n for n, _ in ct.params + lps_]
    psort = dict(ct.params + lps_)
    en_defs = ["% ---- the enabledness of the refined outputs (docs/spec.md §3.7.2)"]
    c_out = []
    for j, r in enumerate(refined_out):
        reads = set(idents(r.cond)) & set(psort)
        args = [n for n in order if n in reads]
        en_defs.append(_fun_def("ix_en_%d" % j, args, psort, "Bool", r.cond))
        c_out.append("ix_en_%d(%s)" % (j, ", ".join(args)) if args else "ix_en_%d" % j)
    q_reads = set()
    for c in c_out:
        q_reads |= set(idents(c)) & set(psort)
    q_args = [n for n in order if n in q_reads]
    en_defs.append(_fun_def("ix_quiet", q_args, psort, "Bool", " && ".join("!%s" % c for c in c_out) or "true"))
    q = "ix_quiet(%s)" % ", ".join(q_args) if q_args else "ix_quiet"
    out_labels = sorted({s.label[2:] for s in outputs})

    def body(priority: bool, extra: str = "") -> str:
        parts = []
        for r in refined_in:
            cond = r.cond if not priority else "(%s) && %s" % (r.cond, q)
            parts.append(_summand_text(r, cond, "input"))
        for j, r in enumerate(refined_out):
            cond = r.cond if not priority else c_out[j]
            if priority and j:
                cond = "%s%s" % (cond, "".join(" && !%s" % c for c in c_out[:j]))
            parts.append(_summand_text(r, cond, "output %s" % r.name))
        return "\n\n".join(("   " if k == 0 else " + ") + p for k, p in enumerate(parts)) + extra + "\n"

    defs = _definitions(policy, indexes, sorted(least_sorts), ct)
    en_text = "\n".join(en_defs) + "\n\n"
    list_params = [(ix.list_param, "List(%s)" % ix.sort) for ix in indexes.values() if ix.list_param]
    head_params = ",\n  ".join("%s: %s" % p for p in ct.params + list_params)
    proc = "proc Core(\n  %s\n) =\n" % head_params
    tail = ct.tail
    gen = _header_block("%")
    refined_text = gen + ct.head + defs + en_text + proc + body(True) + "\n" + tail
    stepper_text = gen + ct.head + defs + proc + body(False) + "\n" + tail
    probe_text, probe_mcf, synced = _probe(ct, policy, refined_in, refined_out, q, defs + en_text, proc, body, gen, out_labels)
    init_suffix = "".join(", []" for _ in list_params)
    return Refined(refined_text, stepper_text, probe_text, probe_mcf, q, refined_in, refined_out, list_params,
                   init_suffix, indexes, [s for s in policy.order if s in policy.defer], out_labels, synced)


def _fun_def(name: str, args: list, psort: dict, res: str, body: str) -> str:
    """A map over the parameters args whose value is body (the parameters renamed to its variables)."""
    if not args:
        return "map %s: %s;\neqn %s = %s;" % (name, res, name, body)
    dom = " # ".join("(%s)" % psort[a] if "->" in psort[a] else psort[a] for a in args)
    vs = {a: "ix_p_%s" % a for a in args}
    return "map %s: %s -> %s;\nvar %s;\neqn %s(%s) = %s;" % (
        name, dom, res, "; ".join("%s: %s" % (vs[a], psort[a]) for a in args), name,
        ", ".join(vs[a] for a in args), _plain_subst_free(body, vs))


def _summand_text(r: Src, cond: str, tag: str) -> str:
    sums = "sum %s .\n    " % ", ".join("%s: %s" % d for d in r.decls) if r.decls else ""
    assigns = ",\n         ".join("%s = %s" % a for a in r.assigns)
    return "%% %s %s\n%s(%s) ->\n    %s .\n    Core(%s)" % (r.name, tag, sums, cond, r.action, assigns)


def _resolve_equalities(cond: str, texts: list, names: list, g, where: str):
    """Substitute every variable of names fixed by a conjunct x == e: (cond, texts, left)."""
    left = list(names)
    progress = True
    while left and progress:
        progress = False
        for x in list(left):
            e = None
            for c in conjuncts(cond):
                sides = split_top(c, "==")
                if len(sides) != 2:
                    continue
                a, b = sides[0].strip(), sides[1].strip()
                for lhs, rhs in ((a, b), (b, a)):
                    while lhs.startswith("(") and matching(lhs, 0) == len(lhs):
                        lhs = lhs[1:-1].strip()
                    if lhs == x and x not in idents(rhs):
                        e = rhs
                        break
                if e is not None:
                    break
            if e is None:
                continue
            m = {x: e}
            cond = subst(cond, m)
            texts = [subst(t, m) for t in texts]
            left.remove(x)
            progress = True
    return cond, texts, left


def fixed_vars(s: Src) -> set:
    """The sum variables of s fixed by a top-level conjunct x == e (§3.5.5)."""
    names = {n for n, _ in s.decls}
    out = set()
    for c in conjuncts(s.cond):
        sides = split_top(c, "==")
        if len(sides) != 2:
            continue
        for lhs, rhs in ((sides[0].strip(), sides[1]), (sides[1].strip(), sides[0])):
            while lhs.startswith("(") and matching(lhs, 0) == len(lhs):
                lhs = lhs[1:-1].strip()
            if lhs in names and lhs not in idents(rhs):
                out.add(lhs)
    return out


def _refine_output(s: Src, policy: Policy, ops, g) -> Src:
    sigma: dict = {}
    whens = []
    cond, action = s.cond, s.action
    rhs = [a for _, a in s.assigns]
    names = [n for n, _ in s.decls]
    for v, term, when in policy.select.get(s.name, []):
        t = ops(subst(term, sigma))
        w = ops(subst(when, sigma))
        sigma[v] = t
        if norm(w) != "true":
            whens.append(w)
    cond = subst(cond, sigma)
    action = subst(action, sigma)
    rhs = [subst(a, sigma) for a in rhs]
    rest = [n for n in names if n not in sigma]
    cond, texts, left = _resolve_equalities(cond, [action] + rhs, rest, g, s.name)
    action, rhs = texts[0], texts[1:]
    if left:
        g("G10", "summand %s" % s.name, "§3.9.1", "refined output with sum variables %s left" % ", ".join(left))
    if whens:
        cond = "(%s) && (%s)" % (" && ".join("(%s)" % w for w in whens), cond)
    return Src(s.kind, s.level, s.label, s.name, [], cond, action, list(zip([n for n, _ in s.assigns], rhs)))


def _refine_input(s: Src, g) -> Src:
    in_action = set(idents(s.action))
    names = [n for n, _ in s.decls if n not in in_action]
    rhs = [a for _, a in s.assigns]
    cond, texts, left = _resolve_equalities(s.cond, [s.action] + rhs, names, g, s.name)
    if left:
        g("G10", "summand %s" % s.name, "§3.9.1", "refined input with sum variables %s neither arguments nor fixed" % ", ".join(left))
    decls = [d for d in s.decls if d[0] in in_action]
    return Src(s.kind, s.level, s.label, s.name, decls, cond, texts[0], list(zip([n for n, _ in s.assigns], texts[1:])))


# ------------------------------------------------------------------ indexes


def _index_infos(policy: Policy, ct: CoreText, params: dict, g) -> dict:
    out = {}
    order = [n for n, _ in ct.params]

    def args_of(term):
        if term is None:
            return []
        xs = set(idents(term)) & set(params)
        return [n for n in order if n in xs]

    def codomain(term, var, what, name):
        if term is None:
            return None
        m = re.fullmatch(r"\s*([A-Za-z_][\w']*)\s*\(\s*" + re.escape(var) + r"\s*\)\s*", term)
        if not m or m.group(1) not in params or "->" not in params[m.group(1)]:
            g("G9", "index.%s" % name, "§3.7.6", "the %s must be a column read at %s, to know its sort" % (what, var))
            return None
        return params[m.group(1)].split("->", 1)[1].strip()

    for name, ix in policy.index.items():
        if ix.sort not in ct.ids:
            continue
        out[name] = IndexInfo(
            name, ix.sort, ct.ids[ix.sort], ix.order, args_of(ix.member), args_of(ix.cls), args_of(ix.key),
            args_of(ix.adm_when), args_of(ix.bound), codomain(ix.cls, ix.var, "class", name),
            codomain(ix.key, ix.var, "key", name), "ix_%s" % name if ix.order in ("newest", "oldest") else None)
    return out


def _union(*lists) -> list:
    seen, out = set(), []
    for l in lists:
        for x in l:
            if x not in seen:
                seen.add(x)
                out.append(x)
    return out


def _all_args(ix: IndexInfo) -> list:
    if ix.list_param:
        return _union(ix.cls_args, ix.adm_args)
    return _union(ix.mem_args, ix.cls_args, ix.key_args, ix.adm_args, ix.bound_args)


def _ok_args(ix: IndexInfo) -> list:
    return _union(ix.mem_args, ix.cls_args, ix.adm_args)


def _definitions(policy: Policy, indexes: dict, least_sorts: list, ct: CoreText) -> str:
    """The reference definitions of §3.7.4 item 2, the maps of the policy, and the
    index maps the upkeep of item 3 uses, all as mCRL2 text."""
    params = dict(ct.params)
    out = ["% ---- the policy's maps and the reference definitions of the selection operations",
           "% (docs/spec.md §3.7.1, §3.7.4)"]
    if policy.maps.strip():
        out.append(policy.maps.strip())
    for srt in least_sorts:
        c = ct.ids[srt]
        out.append("map ix_least_%s: FSet(%s) # Nat -> %s;" % (srt, srt, srt))
        out.append("var ix_s: FSet(%s); ix_i: Nat;" % srt)
        out.append("eqn %s(ix_i) in ix_s -> ix_least_%s(ix_s, ix_i) = %s(ix_i);" % (c, srt, c))
        out.append("    !(%s(ix_i) in ix_s) -> ix_least_%s(ix_s, ix_i) = ix_least_%s(ix_s, ix_i + 1);" % (c, srt, srt))
    for name, info in indexes.items():
        ix = policy.index[name]
        I, C = info.sort, info.ctor
        vmap = {p: "ix_p_%s" % p for p in params}

        def tr(term, var=None, to=None):
            m = dict((p, vmap[p]) for p in idents(term) if p in params)
            if var is not None:
                m[var] = to
            return _plain_subst(term, m)

        def dom(srt):
            return "(%s)" % srt if "->" in srt else srt

        def vlist(args):
            return [vmap[a] for a in args]

        def decl(args, extra):
            vs = ["%s: %s" % (vmap[a], params[a]) for a in args] + extra
            return "var %s;" % "; ".join(vs)

        def mapdecl(n, args, extra_sorts, res):
            ds = [dom(params[a]) for a in args] + [dom(x) for x in extra_sorts]
            return "map %s: %s%s;" % (n, " # ".join(ds) + " -> " if ds else "", res)

        def call(n, args, extra):
            xs = vlist(args) + extra
            return "%s(%s)" % (n, ", ".join(xs)) if xs else n

        out.append("% index " + name)
        out.append(mapdecl("ix_mem_%s" % name, info.mem_args, [I], "Bool"))
        out.append(decl(info.mem_args, ["ix_v: %s" % I]))
        out.append("eqn %s = %s;" % (call("ix_mem_%s" % name, info.mem_args, ["ix_v"]), tr(ix.member, ix.var, "ix_v")))
        if ix.cls is not None:
            out.append(mapdecl("ix_cls_%s" % name, info.cls_args, [I], info.cls_sort))
            out.append(decl(info.cls_args, ["ix_v: %s" % I]))
            out.append("eqn %s = %s;" % (call("ix_cls_%s" % name, info.cls_args, ["ix_v"]), tr(ix.cls, ix.var, "ix_v")))
            out.append(mapdecl("ix_adm_%s" % name, info.adm_args, [info.cls_sort], "Bool"))
            out.append(decl(info.adm_args, ["ix_k: %s" % info.cls_sort]))
            out.append("eqn %s = %s;" % (call("ix_adm_%s" % name, info.adm_args, ["ix_k"]), tr(ix.adm_when, ix.adm_var, "ix_k")))
        if ix.key is not None:
            out.append(mapdecl("ix_key_%s" % name, info.key_args, [I], info.key_sort))
            out.append(decl(info.key_args, ["ix_v: %s" % I]))
            out.append("eqn %s = %s;" % (call("ix_key_%s" % name, info.key_args, ["ix_v"]), tr(ix.key, ix.var, "ix_v")))
        out.append(mapdecl("ix_bound_%s" % name, info.bound_args, [], "Nat"))
        if info.bound_args:
            out.append(decl(info.bound_args, []))
        out.append("eqn %s = %s;" % (call("ix_bound_%s" % name, info.bound_args, []), tr(ix.bound)))
        ok_args = _ok_args(info)
        out.append(mapdecl("ix_ok_%s" % name, ok_args, [I], "Bool"))
        out.append(decl(ok_args, ["ix_v: %s" % I]))
        okv = call("ix_mem_%s" % name, info.mem_args, ["ix_v"])
        if ix.cls is not None:
            okv = "%s && %s" % (okv, call("ix_adm_%s" % name, info.adm_args, [call("ix_cls_%s" % name, info.cls_args, ["ix_v"])]))
        out.append("eqn %s = %s;" % (call("ix_ok_%s" % name, ok_args, ["ix_v"]), okv))
        q = _all_args(info)
        if info.list_param:
            L = "List(%s)" % I
            adm = ("%s" % call("ix_adm_%s" % name, info.adm_args, [call("ix_cls_%s" % name, info.cls_args, ["ix_y"])])
                   if ix.cls is not None else "true")
            out.append(mapdecl("ix_first_%s" % name, [], [L] + [params[a] for a in q], I))
            out.append(mapdecl("ix_some_%s" % name, [], [L] + [params[a] for a in q], "Bool"))
            out.append(decl(q, ["ix_y: %s" % I, "ix_l: %s" % L]))
            qa = ", ".join([""] + vlist(q)) if q else ""
            out.append("eqn ix_first_%s([]%s) = %s(0);" % (name, qa, C))
            out.append("    ix_first_%s(ix_y |> ix_l%s) = if(%s, ix_y, ix_first_%s(ix_l%s));" % (name, qa, adm, name, qa))
            out.append("    ix_some_%s([]%s) = false;" % (name, qa))
            out.append("    ix_some_%s(ix_y |> ix_l%s) = %s || ix_some_%s(ix_l%s);" % (name, qa, adm, name, qa))
            ins = "ix_y |> ix_l" if info.order == "newest" else "ix_l <| ix_y"
            out.append("map ix_upd_%s: %s # %s # Bool # Bool -> %s;" % (name, L, I, L))
            out.append("    ix_rm_%s: %s # %s -> %s;" % (name, L, I, L))
            out.append("var ix_l: %s; ix_y, ix_x: %s; ix_a, ix_b: Bool;" % (L, I))
            out.append("eqn ix_upd_%s(ix_l, ix_y, ix_a, ix_b) = if(ix_b && !ix_a, if(ix_y in ix_l, ix_l, %s), "
                       "if(ix_a && !ix_b, ix_rm_%s(ix_l, ix_y), ix_l));" % (name, ins, name))
            out.append("    ix_rm_%s([], ix_y) = [];" % name)
            out.append("    ix_rm_%s(ix_x |> ix_l, ix_y) = if(ix_x == ix_y, ix_l, ix_x |> ix_rm_%s(ix_l, ix_y));" % (name, name))
            continue
        b = call("ix_bound_%s" % name, info.bound_args, [])
        ok = call("ix_ok_%s" % name, ok_args, ["%s(ix_i)" % C])
        qv = vlist(q)
        qa = ", ".join(qv + [""]) if qv else ""
        out.append(mapdecl("ix_some_%s" % name, q, ["Nat"], "Bool"))
        out.append(decl(q, ["ix_i: Nat"]))
        out.append("eqn ix_i >= %s -> ix_some_%s(%six_i) = false;" % (b, name, qa))
        out.append("    ix_i < %s && %s -> ix_some_%s(%six_i) = true;" % (b, ok, name, qa))
        out.append("    ix_i < %s && !%s -> ix_some_%s(%six_i) = ix_some_%s(%six_i + 1);" % (b, ok, name, qa, name, qa))
        if info.order == "least":
            out.append(mapdecl("ix_first_%s" % name, q, ["Nat"], I))
            out.append(decl(q, ["ix_i: Nat"]))
            out.append("eqn ix_i >= %s -> ix_first_%s(%six_i) = %s(0);" % (b, name, qa, C))
            out.append("    ix_i < %s && %s -> ix_first_%s(%six_i) = %s(ix_i);" % (b, ok, name, qa, C))
            out.append("    ix_i < %s && !%s -> ix_first_%s(%six_i) = ix_first_%s(%six_i + 1);" % (b, ok, name, qa, name, qa))
        else:
            key_i = call("ix_key_%s" % name, info.key_args, ["%s(ix_i)" % C])
            key_c = call("ix_key_%s" % name, info.key_args, ["ix_c"])
            better = "%s && (!ix_f || %s < %s)" % (ok, key_i, key_c)
            out.append(mapdecl("ix_first_%s" % name, q, ["Nat", I, "Bool"], I))
            out.append(decl(q, ["ix_i: Nat", "ix_c: %s" % I, "ix_f: Bool"]))
            out.append("eqn ix_i >= %s -> ix_first_%s(%six_i, ix_c, ix_f) = ix_c;" % (b, name, qa))
            out.append("    ix_i < %s && %s -> ix_first_%s(%six_i, ix_c, ix_f) = ix_first_%s(%six_i + 1, %s(ix_i), true);"
                       % (b, better, name, qa, name, qa, C))
            out.append("    ix_i < %s && !(%s) -> ix_first_%s(%six_i, ix_c, ix_f) = ix_first_%s(%six_i + 1, ix_c, ix_f);"
                       % (b, better, name, qa, name, qa))
    return "\n".join(out) + "\n\n"


def _plain_subst(text: str, mapping: dict) -> str:
    """Identifier renaming (no parentheses added): for names in index terms."""
    return _IDENT.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)


def _upkeep(r: Src, indexes: dict, params: dict, columns: set, sets: set, g) -> None:
    """Append the assignment keeping each newest/oldest index list (§3.7.4 item 3)."""
    assigned = dict(r.assigns)
    for name, info in indexes.items():
        if not info.list_param:
            continue
        touched = [p for p in info.mem_args if p in assigned]
        if not touched:
            continue
        ids = []
        for p in touched:
            got, why = _written_ids(assigned[p], p, p in sets)
            if why:
                g("G13", "summand %s, %s" % (r.name, p), "§3.7.4", "index %s reads %s, written here %s" % (name, p, why))
                return
            for y in got:
                if norm(y) not in [norm(x) for x in ids]:
                    ids.append(y)
        if len(ids) > 1:
            g("G13", "summand %s" % r.name, "§3.7.4",
              "index %s: the summand writes its membership at %d ids (%s); one at most is supported"
              % (name, len(ids), ", ".join(norm(y) for y in ids)))
            return
        if not ids:
            continue
        y = ids[0]
        old = "ix_mem_%s(%s)" % (name, ", ".join(info.mem_args + ["(%s)" % y]))
        new = "ix_mem_%s(%s)" % (name, ", ".join(["(%s)" % assigned[p] if p in assigned else p for p in info.mem_args]
                                                  + ["(%s)" % y]))
        r.assigns.append((info.list_param, "ix_upd_%s(%s, %s, %s, %s)" % (name, info.list_param, "(%s)" % y, old, new)))


def _written_ids(rhs: str, p: str, is_set: bool):
    """(ids, None) the ids a right side writes in p, or ([], why) when it writes p
    in a way the upkeep cannot follow (G13). The right side must be built from p by
    function updates `p[i -> v]` (a column) or `p + {x}`, `p - {x}` (a set), under
    `if`; whatever the indices, values and conditions read is a read."""
    ids, stack = [], [rhs]
    while stack:
        e = stack.pop().strip()
        while e.startswith("(") and matching(e, 0) == len(e):
            e = e[1:-1].strip()
        if e == p:
            continue
        m = re.match(r"if\s*\(", e)
        if m and matching(e, m.end() - 1) == len(e):
            parts = split_top(e[m.end():-1], ",")
            if len(parts) == 3:
                stack.extend([parts[2], parts[1]])
                continue
        if not is_set and e.endswith("]"):
            depth, k = 0, len(e) - 1
            while k >= 0:
                if e[k] in ")]}":
                    depth += 1
                elif e[k] in "([{":
                    depth -= 1
                    if depth == 0:
                        break
                k -= 1
            parts = split_top(e[k + 1:-1], "->")
            if k > 0 and len(parts) == 2:
                ids.append(parts[0].strip())
                stack.append(e[:k])
                continue
        if is_set:
            parts = split_top(e, " + ")
            minus = split_top(e, " - ")
            mr = minus[1].strip() if len(minus) == 2 else ""
            if mr.startswith("{") and matching(mr, 0) == len(mr) and len(split_top(mr[1:-1], ",")) == 1:
                ids.append(minus[1].strip()[1:-1].strip())
                stack.append(minus[0])
                continue
            if len(parts) == 2:
                l, r = parts[0].strip(), parts[1].strip()
                if r.startswith("{") and matching(r, 0) == len(r) and len(split_top(r[1:-1], ",")) == 1:
                    ids.append(r[1:-1].strip())
                    stack.append(l)
                    continue
                if l.startswith("{") and matching(l, 0) == len(l) and len(split_top(l[1:-1], ",")) == 1:
                    ids.append(l[1:-1].strip())
                    stack.append(r)
                    continue
        return [], "other than at ids visible in the assignment (a walk, or a whole-set operation): %s" % norm(e)[:80]
    return ids, None


# ------------------------------------------------------------------ the probe Core (§3.9.3, §3.9.6, §3.9.7)


def _probe(ct: CoreText, policy: Policy, refined_in, refined_out, q, defs, proc, body, gen, out_labels):
    """The probe Core and probe.mcf. The probes read the source summands with their
    equality-fixed variables that are not action arguments substituted (as sum
    elimination does), so no probe enumerates a list. Input probes take their
    arguments from the environment: `c_x_refused | e_x -> refused_x` (§3.6.8), and
    likewise `c_x_ambiguous | e_x -> ambiguous_x` for two input summands of one label."""
    eqsub = []
    for s in ct.summands:
        r = _refine_input(s, lambda *a: None)
        r.kind = s.kind
        eqsub.append(r)
    extra = []
    acts = ["probe_enabled", "probe_quiet", "probe_ambiguous", "probe_refused", "probe_ambiguous_in"]
    deferred = [n for n in policy.order if n in policy.defer]
    for s in eqsub:
        if s.kind != "output":
            continue
        if s.name in policy.defer:
            extra.append(_probe_summand(s.decls, "(%s) && (%s)" % (s.cond, q), "probe_deferred_%d" % deferred.index(s.name)))
        else:
            extra.append(_probe_summand(s.decls, s.cond, "probe_enabled"))
    for k in range(len(deferred)):
        acts.append("probe_deferred_%d" % k)
    extra.append("(%s) -> probe_quiet . Core()" % q)
    groups: dict = {}
    for s in eqsub:
        groups.setdefault((s.kind, s.label, _arity(s.action)), []).append(s)
    synced = []  # (core label, environment label, vocabulary label, sorts)
    for (kind, label, n), ss in groups.items():
        sorts = next((x for x in ct.acts.get(label, []) if len(x) == n), [])
        vs = ["rf_%d" % k for k in range(n)]
        if kind == "output":
            # §3.9.6 for outputs: two summands enabled with the same arguments.
            for i in range(len(ss)):
                for j in range(i + 1, len(ss)):
                    a, b = ss[i], _renamed(ss[j], "_2")
                    eqs = " && ".join("(%s) == (%s)" % (x, y) for x, y in zip(_args(a.action), _args(b.action))) or "true"
                    extra.append(_probe_summand(a.decls + b.decls, "(%s) && (%s) && %s" % (a.cond, b.cond, eqs), "probe_ambiguous"))
            continue
        acc = [_accepts(s, vs) for s in ss]
        decls = list(zip(vs, sorts))
        voc = label[2:]
        # §3.9.7: refused in a quiescent state. The event is taken together with a
        # marker and Core stops: mCRL2 refuses an environment action synchronised
        # in two ways (`c_x | e_x` and `c_x_refused | e_x`, as §3.6.8 writes it).
        extra.append(_probe_summand(decls, "(%s) && !(%s)" % (q, " || ".join("(%s)" % a for a in acc)),
                                    _act(label, vs) + "|probe_refused", "delta"))
        synced.append((voc, "probe_refused", sorts))
        # §3.9.6 for inputs: accepted by two summands.
        if len(ss) > 1:
            pairs = " || ".join("((%s) && (%s))" % (acc[i], acc[j]) for i in range(len(acc)) for j in range(i + 1, len(acc)))
            extra.append(_probe_summand(decls, pairs, _act(label, vs) + "|probe_ambiguous_in", "delta"))
            synced.append((voc, "probe_ambiguous_in", sorts))
    act_text = "act %s;\n" % ", ".join(acts)
    probe_text = gen + ct.head + defs + act_text + "\n" + proc + body(True, "".join("\n + " + e for e in extra)) + "\n" + ct.tail
    out = " || ".join(_exists_act(lb, ss) for lb, ss in _out_sigs(ct))
    bad = " && ".join("([true*][%s] false)" % _exists_act(voc, sorts, marker) for voc, marker, sorts in synced)
    mcf = (_header_block("%") +
           "%% Non-blocking, one summand per label, receptive, and every step ends, on the probe system.\n"
           "([true*] !(<probe_enabled>true && <probe_quiet>true))\n"
           "&& ([true*][probe_ambiguous] false)\n"
           "&& %s\n"
           "&& ([true*] mu X . [%s] X)\n" % (bad or "true", out))
    return probe_text, mcf, synced


def _act(name: str, vs: list) -> str:
    return "%s(%s)" % (name, ", ".join(vs)) if vs else name


def _exists_act(label: str, sorts: list, marker: str = "") -> str:
    m = "|" + marker if marker else ""
    if not sorts:
        return label + m
    xs = ["x%d" % k for k in range(len(sorts))]
    return "exists %s . %s(%s)%s" % (", ".join("%s: %s" % (x, s) for x, s in zip(xs, sorts)), label, ", ".join(xs), m)


def _out_sigs(ct: CoreText) -> list:
    seen, out = set(), []
    for s in ct.summands:
        if s.kind != "output":
            continue
        n = _arity(s.action)
        if (s.label, n) in seen:
            continue
        seen.add((s.label, n))
        sorts = next((ss for ss in ct.acts.get(s.label, []) if len(ss) == n), [])
        out.append((s.label[2:], sorts))
    return out


def _probe_summand(decls, cond, act, then: str = "Core()") -> str:
    sums = "sum %s . " % ", ".join("%s: %s" % d for d in decls) if decls else ""
    return "%s(%s) -> %s . %s" % (sums, cond, act, then)


def _args(action: str) -> list:
    k = action.find("(")
    if k < 0:
        return []
    return [a.strip() for a in split_top(action[k + 1:matching(action, k) - 1], ",")]


def _arity(action: str) -> int:
    return len(_args(action))


def _renamed(s: Src, suffix: str) -> Src:
    m = {n: n + suffix for n, _ in s.decls}
    ren = lambda t: _plain_subst_free(t, m)  # noqa: E731
    return Src(s.kind, s.level, s.label, s.name, [(n + suffix, t) for n, t in s.decls], ren(s.cond), ren(s.action),
               [(p, ren(a)) for p, a in s.assigns])


def _plain_subst_free(text: str, mapping: dict) -> str:
    regions = _binder_regions(text)
    out, last = [], 0
    for m in _IDENT.finditer(text):
        x = m.group(0)
        if x not in mapping or any(a <= m.start() < b and x in names for a, b, names in regions):
            continue
        out.append(text[last:m.start()])
        out.append(mapping[x])
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def _accepts(s: Src, vs: list) -> str:
    """The condition under which input summand s accepts the event with argument
    values vs: its sum variables bound by the arguments where they are whole
    arguments, the rest quantified (§3.9.7)."""
    args = _args(s.action)
    m, eqs = {}, []
    for v, a in zip(vs, args):
        a0 = a.strip()
        if re.fullmatch(r"[A-Za-z_][\w']*", a0) and any(a0 == n for n, _ in s.decls) and a0 not in m:
            m[a0] = v
        else:
            eqs.append((v, a0))
    cond = subst(s.cond, m)
    rest = [(n, t) for n, t in s.decls if n not in m]
    body = " && ".join(["(%s)" % cond] + ["%s == (%s)" % (v, subst(a, m)) for v, a in eqs])
    if rest:
        return "exists %s . %s" % (", ".join("%s: %s" % d for d in rest), body)
    return body
