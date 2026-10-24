"""The checks of docs/spec.md §3.9 decided on explored state spaces; see
naima-codegen check-refinement, which runs them."""

import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path



def tool(name: str) -> str:
    p = shutil.which(name)
    if p is None:
        alt = Path("/Applications/mCRL2.app/Contents/bin") / name
        if alt.exists():
            return str(alt)
        raise RuntimeError("%s is not installed" % name)
    return p


def explore(src: Path, d: Path) -> Path:
    lps, aut = d / (src.stem + ".lps"), d / (src.stem + ".aut")
    for cmd in ([tool("mcrl22lps"), "-q", str(src), str(lps)], [tool("lps2lts"), "-q", str(lps), str(aut)]):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError("%s: %s" % (cmd[0], r.stderr[-1500:]))
    return aut


def read_aut(aut: Path):
    lines = aut.read_text().splitlines()
    head = re.match(r"des \((\d+),\s*(\d+),\s*(\d+)\)", lines[0])
    edges = []
    for line in lines[1:]:
        m = re.match(r'\((\d+),"(.*)",(\d+)\)$', line.strip())
        edges.append((int(m.group(1)), m.group(2), int(m.group(3))))
    return int(head.group(3)), edges


def output_labels(proj) -> set:
    man = json.loads((proj.gen_dir / "MANIFEST.json").read_text())
    return {s["name"].split("#")[0][2:] for s in man["summands"] if s["kind"] == "output"}


def no_output_cycle(n: int, edges: list, outs: set) -> bool:
    """No cycle of output transitions (iterative colouring)."""
    succ = [[] for _ in range(n)]
    for a, lbl, b in edges:
        if lbl.split("(")[0] in outs:
            succ[a].append(b)
    colour = [0] * n
    for s0 in range(n):
        if colour[s0]:
            continue
        stack = [(s0, 0)]
        colour[s0] = 1
        while stack:
            s, k = stack.pop()
            if k < len(succ[s]):
                stack.append((s, k + 1))
                t = succ[s][k]
                if colour[t] == 1:
                    return False
                if colour[t] == 0:
                    colour[t] = 1
                    stack.append((t, 0))
            else:
                colour[s] = 2
    return True


def blocked_states(n: int, edges: list) -> list:
    """§3.9.3: the states of a probe system where the model allows an output and
    the refined Core has none."""
    labels = [set() for _ in range(n)]
    for a, l, _ in edges:
        labels[a].add(l)
    return [s for s in range(n) if "probe_enabled" in labels[s] and "probe_quiet" in labels[s]]


def observer_labels(proj) -> list:
    """The observers' labels, o_*, declared in the model directory's observers.mcrl2 (§3.9.2)."""
    p = proj.path(proj.m("observers.mcrl2"))
    if not p.is_file():
        return []
    acts = p.read_text(encoding="utf-8").split("% @summands", 1)[0]
    return sorted(set(re.findall(r"\b(o_[A-Za-z0-9_]+)\b", acts)))


def check(proj, group: str, work: Path, say=print, model: bool = True) -> bool:
    """Every check of §3.9 on one group's systems under <generated>/model/systems."""
    sysdir = proj.gen_dir / "model" / "systems"
    ok = True
    t0 = time.time()
    auts = {}
    for kind in (("model",) if model else ()) + ("refined", "probe"):
        auts[kind] = explore(sysdir / ("%s-%s.mcrl2" % (group, kind)), work)
        n, e = read_aut(auts[kind])
        say("%s %s: %d states, %d transitions (%.0f s)" % (group, kind, n, len(e), time.time() - t0))
    n, edges = read_aut(auts["refined"])
    obs = sorted({l.split("(")[0] for _, l, _ in edges if l.startswith("o_")})
    say("%s observers on the refined system: %s" % (group, "none" if not obs else "FIRED " + ", ".join(obs)))
    ok &= not obs
    hide = ",".join(observer_labels(proj))
    if model:
        r = subprocess.run([tool("ltscompare"), "--preorder=trace-ac"] + (["--tau=" + hide] if hide else [])
                           + [str(auts["refined"]), str(auts["model"])],
                           capture_output=True, text=True)
        inc = r.stdout.strip().endswith("true")
        say("%s §3.9.2 trace inclusion: %s" % (group, "holds" if inc else "FAILS: " + (r.stdout + r.stderr)[-500:]))
        ok &= inc
    else:
        say("%s §3.9.2 trace inclusion: not checked (--no-model)" % group)
    ends = no_output_cycle(n, edges, output_labels(proj))
    say("%s §3.9.5 every step ends: %s" % (group, "holds" if ends else "FAILS (a cycle of outputs)"))
    ok &= ends
    n, edges = read_aut(auts["probe"])
    labels = [[] for _ in range(n)]
    for a, l, _ in edges:
        labels[a].append(l)
    blocked = blocked_states(n, edges)
    say("%s §3.9.3 non-blocking: %s" % (group, "holds" if not blocked else "FAILS in %d states, e.g. %d" % (len(blocked), blocked[0])))
    ok &= not blocked
    deferred = {}
    for s in range(n):
        for l in set(labels[s]):
            if l.startswith("probe_deferred_"):
                deferred[l] = deferred.get(l, 0) + 1
    say("%s §3.9.3 deferred (states, reported): %s" % (group, json.dumps(deferred, sort_keys=True)))
    amb = sum(1 for _, l, _ in edges if l == "probe_ambiguous" or l.endswith("|probe_ambiguous_in") or l.startswith("probe_ambiguous_in|"))
    say("%s §3.9.6 one summand per label: %s" % (group, "holds" if not amb else "FAILS (%d transitions)" % amb))
    ok &= not amb
    ref = [l for _, l, _ in edges if "probe_refused" in l]
    say("%s §3.9.7 receptive: %s" % (group, "holds" if not ref else "FAILS (%d), e.g. %s" % (len(ref), ref[0])))
    ok &= not ref
    return ok




def property_items(proj) -> dict:
    """{model group: [property file]} from the property items under [project]
    properties (§3.6.9, §3.9.4): an item names its group by "group", or by
    "model": "<model>/systems/<group>.mcrl2"."""
    out: dict = {}
    if proj.properties is None:
        return out
    for meta in sorted(proj.properties.glob("*/meta.json")):
        d = json.loads(meta.read_text())
        m = re.fullmatch(re.escape(proj.model) + r"/systems/([\w-]+)\.mcrl2", d.get("model", ""))
        g = d.get("group") if isinstance(d.get("group"), str) else m.group(1) if m else None
        if g and d.get("verifier") == "mcrl2" and d.get("property"):
            out.setdefault(g, [])
            if d["property"] not in out[g]:
                out[g].append(d["property"])
    return out


def verify_properties(proj, group: str, work: Path, say=print) -> bool:
    """§3.9.4: every property the model verifies on model/systems/<group>.mcrl2,
    verified again on generated/model/systems/<group>-refined.mcrl2. The state
    space is explored once and each formula is checked on it (lts2pbes): from
    the LPS, pbessolve stops with a bus error on the refined systems (mCRL2
    202607.0, 2026-10-08), even on 159 states."""
    props = property_items(proj).get(group, [])
    src = proj.gen_dir / "model" / "systems" / ("%s-refined.mcrl2" % group)
    lps, lts = work / ("%s-refined-p.lps" % group), work / ("%s-refined-p.lts" % group)
    for cmd in ([tool("mcrl22lps"), "-q", str(src), str(lps)], [tool("lps2lts"), "-q", str(lps), str(lts)]):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError("%s: %s" % (cmd[0], r.stderr[-1500:]))
    ok = True
    for prop in props:
        t0 = time.time()
        pbes = work / "p.pbes"
        r = subprocess.run([tool("lts2pbes"), "-f", str(proj.root / prop), str(lts), str(pbes)], capture_output=True, text=True)
        if r.returncode:
            say("%s §3.9.4 %s: lts2pbes failed: %s" % (group, prop, r.stderr[-300:]))
            ok = False
            continue
        r = subprocess.run([tool("pbessolve"), str(pbes)], capture_output=True, text=True)
        verdict = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "error %d: %s" % (r.returncode, r.stderr[-300:])
        say("%s §3.9.4 %s: %s (%.0f s)" % (group, prop, "holds" if verdict == "true" else "FAILS (%s)" % verdict, time.time() - t0))
        ok &= verdict == "true"
    return ok
