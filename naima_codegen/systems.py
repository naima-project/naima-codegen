"""The systems the checks of §3.9 run on (docs/spec.md §3.9.7, §3.17):
a Core (the model's, the refined one, or the probe one), the model's
`env.mcrl2` and the instances of one group, concatenated: the id sorts bounded
to the group's universe, the instances' values renamed apart, `init System`.

- `model`: Core as written, without the step structure or the observers: the
  system whose traces the refined system's must be (§3.9.2);
- `refined`: the refined Core with the observers of the model's
  `observers.mcrl2` (QUIESCENT read as the refined Core's Q), on which the
  properties are verified again (§3.9.4);
- `probe`: the probe Core, with the probe labels added to `comm` and `allow`
  (§3.6.8), for `<generated>/model/probe.mcf`.

The groups are those of [systems.groups] in codegen.toml, then one group for
each instance no group lists (§3.17). How the id sorts are bounded is
[systems.ids.<sort>] (`IdBounds`).

The text conventions the files follow: an instance is `instances/<name>.mcrl2`,
`@I` in it stands for its constant `i_<name>`, and `% @<header> n` gives the
size of an id sort; Core holds the placeholder `%@observers` where the
observers' summands go; the environment has `proc CoreStart` calling `Core(`,
the placeholder `%@obs-allow` in its allow set and a `comm({ ` list; the
observers file declares its actions after `% @act`, its summands after
`% @summands`, and what follows `% @witness` is left out.
"""

from __future__ import annotations

import re

from .refine import matching


class IdBounds:
    """How the id sorts are bounded in a system (§3.17): for each id sort of
    the declaration, in its order, the prefix of its constants (default: the
    sort's first letter), the instance header that gives the bound (default:
    the sort's name in lower case with an s, `% @<header> n`) and the bound
    when no instance of the group gives one ([systems.ids.<sort>] of codegen.toml)."""

    def __init__(self, id_sorts: list, conf: dict | None = None):
        conf = conf or {}
        self.sorts = [(s, conf.get(s, {}).get("prefix", s[0]), conf.get(s, {}).get("header", s.lower() + "s"),
                       conf.get(s, {}).get("bound")) for s in id_sorts]

    def bounds(self, texts: list) -> list:
        """[(sort, prefix, size)] for the instances' texts."""
        out = []
        for sort, prefix, header, default in self.sorts:
            got = [int(m.group(1)) for t in texts if (m := re.search(r"^% @" + header + r" (\d+)", t, re.M))]
            if len(got) < len(texts) and default is None:
                raise ValueError("an instance has no %% @%s header and [systems.ids.%s] gives no bound" % (header, sort))
            out.append((sort, prefix, max(got + ([default] if len(got) < len(texts) else []))))
        return out


_RECORD = r"sort %s = struct (\w+)\((\w+): Nat\);"


def finite_ids(core: str, bounds: list) -> tuple[str, list]:
    """Id sorts bounded to the instance universe: each `sort S = struct c(p: Nat);`
    becomes the constants <prefix>0, <prefix>1, ... with c and p kept as maps.
    Also the constructors, to rewrite the instances' `c(k)`."""
    ctors = []
    for sort, prefix, count in bounds:
        m = re.search(_RECORD % re.escape(sort), core)
        if m is None:
            raise ValueError("no declaration sort %s = struct ctor(proj: Nat);" % sort)
        ctor, proj = m.group(1), m.group(2)
        cons = " | ".join("%s%d" % (prefix, i) for i in range(count))
        eqs = " ".join("%s(%d) = %s%d; %s(%s%d) = %d;" % (ctor, i, prefix, i, proj, prefix, i, i) for i in range(count))
        core = core.replace(m.group(0), "sort %s = struct %s;\nmap %s: Nat -> %s; %s: %s -> Nat;\neqn %s" % (
            sort, cons, ctor, sort, proj, sort, eqs), 1)
        ctors.append((ctor, prefix))
    return core, ctors


def assemble(core: str, env: str, instances: list, ids: IdBounds, *, init_suffix: str = "", observers: str | None = None,
             quiescent: str = "true", comm: list = (), allow: list = ()) -> str:
    """One system: instances is [(name, text)]; comm is [(core label, environment label, result)]."""
    texts = [t for _, t in instances]
    core, ctors = finite_ids(core, ids.bounds(texts))
    obs_labels = []
    if observers is not None:
        acts, summands = observers.split("% @summands", 1)
        summands = summands.partition("% @witness")[0]  # witnesses: model/systems/<name>-wit only
        acts = acts.split("% @act", 1)[1]
        obs_labels = sorted(set(re.findall(r"\b(o_[A-Za-z0-9_]+)\b", acts)))
        core = core.replace("proc Core(", acts.strip() + "\n\nproc Core(", 1)
        core = core.replace("%@observers", summands.rstrip().replace("QUIESCENT", quiescent), 1)
    if init_suffix:
        k = env.index("proc CoreStart")
        c = env.index("Core(", k)
        e = matching(env, c + 4)
        env = env[:e - 1] + init_suffix + env[e - 1:]
    extra = obs_labels + list(allow)
    env = env.replace("%@obs-allow", "".join(", " + x for x in extra), 1)
    if comm:
        env = env.replace("comm({ ", "comm({ " + "".join("%s | %s -> %s,\n         " % c for c in comm), 1)
    consts = ["i_" + n for n, _ in instances]
    parts = []
    for n, t in instances:
        for ctor, prefix in ctors:
            t = re.sub(r"\b%s\((\d+)\)" % re.escape(ctor), prefix + r"\1", t)
        parts.append(t.replace("@I", "i_" + n))
    head = ("%% GENERATED by naima-codegen (docs/spec.md §3.9.7) from the Core named in its\n"
            "%% file name, the model's env.mcrl2 and instances/{%s}. Do not edit.\n\n"
            "sort Inst = struct %s;\n\n" % (", ".join(n + ".mcrl2" for n, _ in instances), " | ".join(consts)))
    return head + core + "\n" + env + "\n" + "\n".join(parts) + "\ninit System;\n"


def groups(instances: dict, listed: dict | None = None) -> dict:
    """{group name: [instance names]}: the groups of [systems.groups] in their
    order, then one group of its own for every instance no group lists, by
    name (§3.17). An instance may be in more than one group."""
    out = {}
    for g, members in (listed or {}).items():
        unknown = sorted(set(members) - set(instances))
        if unknown:
            raise ValueError("group %s lists %s, not in the instances" % (g, ", ".join(unknown)))
        if not members:
            raise ValueError("group %s is empty" % g)
        out[g] = sorted(set(members))
    seen = {n for ms in out.values() for n in ms}
    for n in sorted(instances):
        if n not in seen:
            if n in out:
                raise ValueError("%s is a group name and an instance in no group" % n)
            out[n] = [n]
    return out
