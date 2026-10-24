"""The parsed form of a linear process as text: what the golden tests record.

Deterministic: every collection in a fixed order, no path, no time.
"""

from __future__ import annotations

from .errors import GeneratorError
from .expr import show, sort_str
from .lps import Lps


def render_lps(lps: Lps) -> str:
    out = []
    out.append("# sorts")
    for name in sorted(lps.structs):
        st = lps.structs[name]
        cons = " | ".join(
            c + ("(%s)" % ", ".join(("%s: %s" % (p, sort_str(s))) if p else sort_str(s) for p, s in f) if f else "") + ("?" + r if r else "")
            for c, f, r in st.constructors)
        out.append("sort %s = struct %s;  %% %s" % (name, cons, st.shape))
    for name in sorted(lps.aliases):
        out.append("sort %s = %s;" % (name, sort_str(lps.aliases[name])))
    out.append("# maps")
    for m in sorted(lps.maps, key=lambda m: (m.name, sort_str(m.sort))):
        out.append("map %s: %s;" % (m.name, sort_str(m.sort)))
    out.append("# equations")
    eqs = []
    for e in lps.equations:
        c = "" if show(e.cond) == "true" else show(e.cond) + " -> "
        eqs.append("%s%s = %s;" % (c, show(e.lhs), show(e.rhs)))
    out.extend(sorted(eqs))
    out.append("# actions")
    for a in sorted(lps.acts, key=lambda a: (a.name, len(a.sorts))):
        out.append("act %s%s;" % (a.name, (": " + " # ".join(sort_str(s) for s in a.sorts)) if a.sorts else ""))
    out.append("# parameters")
    for (n, s), v in zip(lps.params, lps.init):
        out.append("%s: %s = %s" % (n, sort_str(s), show(v)))
    if lps.globals:
        out.append("# global variables")
        for g in lps.globals:
            out.append("%s: %s" % (g.name, sort_str(g.sort)))
    out.append("# summands (%d, delta summands %d)" % (len(lps.summands), lps.deltas))
    for s in lps.summands:
        out.append("summand %s" % s.name)
        if s.sumvars:
            out.append("  sum " + ", ".join("%s: %s" % (n, sort_str(t)) for n, t in s.sumvars))
        out.append("  cond " + show(s.cond))
        out.append("  act  %s(%s)%s" % (s.label, ", ".join(show(a) for a in s.args),
                                        "" if s.n_actions == 1 and not s.timed else "  %% actions %d, timed %s" % (s.n_actions, s.timed)))
        for n, e in s.assigns:
            out.append("  %s := %s" % (n, show(e)))
    return "\n".join(out) + "\n"


def render_refusals(err: GeneratorError | None) -> str:
    if err is None:
        return "# accepted\n"
    return "# refused (%d)\n" % len(err.diagnostics) + "".join(str(d) + "\n" for d in err.diagnostics)
