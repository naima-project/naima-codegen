"""The generated stepper (docs/spec.md §3.8, §3.9.9, §3.16), on the example
model examples/jobqueue.

- refined replay (§3.9.9 item 2): seeded random walks of the refined systems
  (<generated>/model/systems/*-refined.mcrl2, explored by lps2lts) projected to
  events: the Python backend's stepper, in audit mode (every index against its
  definition after every summand, §3.9.9 item 1), takes exactly the actions of
  the walk, and the acceptor of the unrefined Core accepts every entry (§3.9.8);
- the policy's checks (G9, G13) and a policy that blocks where the model acts.

The cross-check of the three backends (§3.16.1, §3.16.5) is in
test_codegen_typescript.py; the helpers it shares with this file are here.
"""

import functools
import importlib.util
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "jobqueue"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codegen_values import parse_printed, to_python, unbind_ids  # noqa: E402
from naima_codegen.frontend import run  # noqa: E402
from naima_codegen.lower import Types  # noqa: E402
from naima_codegen.toolchain import Toolchain  # noqa: E402

PIN = "202607.0"
GEN = EXAMPLE / "generated"
PACKAGE = "jobqueue"  # [project] of the example's codegen.toml
WALKS, STEPS = 80, 600
OBSERVERS = ("o_",)  # the observers' labels, which are not Core's


def _pinned() -> bool:
    return Toolchain().version() == PIN


def _python_backend(gen=GEN, package=PACKAGE):
    """The generated Python package of an example, loaded from its file."""
    spec = importlib.util.spec_from_file_location("%s_generated" % package, gen / "python" / package / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _config(inst_text):
    """The configuration of an instance of the example: its number of workers."""
    return {"w": int(re.search(r"I_w\(@I\) = (\d+);", inst_text).group(1))}


@functools.lru_cache(maxsize=None)
def _ids_conf():
    from naima_codegen.project import load

    return load(EXAMPLE).cfg.systems["ids"]


def _entry(lbl, inputs, outputs, types):
    """(side, (code, args...)) of a transition label of Core, or None."""
    tree = parse_printed(lbl)
    if tree[0] == "app":
        head, args = tree[1], tree[2]
    elif tree[0] in ("id", "name", "const"):
        head, args = tree[1], []
    else:
        head, args = lbl, []
    for side, labels in ((0, inputs), (1, outputs)):
        for code, a in enumerate(labels):
            if a.label == "c_" + head and len(a.sorts) == len(args):
                return side, (code,) + tuple(to_python(x, s, types) for x, s in zip(args, a.sorts))
    return None


# Explore at most the first EXPLORE_MAX states breadth-first (the example's systems
# are smaller) and walk that. A walk reaching an unexplored state ends there, as a
# walk cut inside a step does.
EXPLORE_MAX = 800


@functools.lru_cache(maxsize=None)
def _edges(system: Path) -> dict:
    """The transitions of the first EXPLORE_MAX states of a system's LTS, by source state."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        shutil.copyfile(system, p / "m.mcrl2")
        for cmd in (["mcrl22lps", "-q", "m.mcrl2", "m.lps"], ["lps2lts", "-q", "--max=%d" % EXPLORE_MAX, "m.lps", "m.aut"]):
            r = subprocess.run(cmd, cwd=d, capture_output=True, text=True)
            if r.returncode:
                raise AssertionError(r.stderr[-2000:])
        edges: dict = {}
        for line in (p / "m.aut").read_text().splitlines()[1:]:
            mm = re.match(r'\((\d+),"(.*)",(\d+)\)$', line.strip())
            edges.setdefault(int(mm.group(1)), []).append((mm.group(2), int(mm.group(3))))
    return edges


def _walks(system: Path, seed: int):
    """Random walks of a system's LTS (its first EXPLORE_MAX states): lists of labels, from the initial state."""
    edges = _edges(system)
    rnd = random.Random(seed)
    out = []
    for _ in range(WALKS):
        s, labels = 0, []
        for _ in range(STEPS):
            if s not in edges:
                break
            lbl, s = rnd.choice(edges[s])
            labels.append(lbl)
        out.append(labels)
    return out


def _fresh(m, config, audit=True):
    """A stepper in its initial state, and the actions its constructor took."""
    out = []
    return m.State(config, out, audit), out


class RefinedReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not _pinned():
            raise unittest.SkipTest("the pinned mCRL2 toolset %s is not installed" % PIN)
        cls.model = run(EXAMPLE)
        cls.types = Types(cls.model.lps, cls.model.config.id_sorts)
        cls.m = _python_backend()
        cls.inputs = sorted((a for a in cls.model.actions if a.kind == "input"), key=lambda a: a.label)
        cls.outputs = sorted((a for a in cls.model.actions if a.kind == "output"), key=lambda a: a.label)

    def _replay(self, group: str):
        m = self.m
        insts = {p.stem: p.read_text() for p in (EXAMPLE / "model" / "instances").glob("*.mcrl2")}
        events = 0
        for w, labels in enumerate(_walks(GEN / "model" / "systems" / ("%s-refined.mcrl2" % group), 20261008)):
            pick = re.fullmatch(r"pick\(i_(\w+)\)", labels[0])
            cfg = m.Config(**_config(insts[pick.group(1)]))
            st, out = _fresh(m, cfg)
            acc = m.Acceptor(cfg)
            want, k = [], 1
            # The constructor's actions, then per event the actions up to the next event.
            entries = [_entry(unbind_ids(lbl, self.model, _ids_conf()), self.inputs, self.outputs, self.types) for lbl in labels[1:]
                       if not lbl.startswith(OBSERVERS)]
            self.assertNotIn(None, entries, "a label that is not Core's")
            i = 0
            while i < len(entries) and entries[i][0] == 1:
                want.append(entries[i][1])
                i += 1
            self.assertEqual(out, want, "%s walk %d: the constructor's actions" % (group, w))
            for e in out:
                acc.accept((1, e))
            while i < len(entries):
                ev = entries[i][1]
                i += 1
                want = []
                while i < len(entries) and entries[i][0] == 1:
                    want.append(entries[i][1])
                    i += 1
                out = []
                acc.accept((0, ev))
                try:
                    st.step(ev, out)
                except m.AuditFailed as e:
                    self.fail("%s walk %d event %r: %s" % (group, w, ev, e))
                if i < len(entries) or len(out) <= len(want):
                    self.assertEqual(out, want, "%s walk %d event %r" % (group, w, ev))
                else:  # the walk was cut inside the step
                    self.assertEqual(out[:len(want)], want, "%s walk %d event %r" % (group, w, ev))
                for a in out:
                    acc.accept((1, a))
                events += 1
            k += 1
        return events

    def test_small(self):
        self.assertGreater(self._replay("small"), 250)

    def test_flaky(self):
        self.assertGreater(self._replay("flaky"), 150)


def _policy_root(edit, core_edit=None) -> Path:
    """A copy of the example with model/policy.toml (and model/core.mcrl2) changed by edit(text)."""
    d = Path(tempfile.mkdtemp(prefix="naima-codegen-policy-"))
    shutil.copytree(EXAMPLE / "model", d / "model")
    shutil.copyfile(EXAMPLE / "codegen.toml", d / "codegen.toml")
    p = d / "model" / "policy.toml"
    p.write_text(edit(p.read_text()))
    if core_edit is not None:
        c = d / "model" / "core.mcrl2"
        c.write_text(core_edit(c.read_text()))
    return d


class Policy(unittest.TestCase):
    """The policy's checks (§3.7.1, §3.7.4, §3.7.6, §3.14.1: G9, G13) and a policy
    that leaves the model refused by the non-blocking check (§3.9.3)."""

    @classmethod
    def setUpClass(cls):
        if not _pinned():
            raise unittest.SkipTest("the pinned mCRL2 toolset %s is not installed" % PIN)
        cls.model = run(EXAMPLE)

    def _codes(self, edit, core_edit=None) -> list:
        from naima_codegen import outputs
        from naima_codegen.errors import GeneratorError
        from naima_codegen.project import load

        d = _policy_root(edit, core_edit)
        try:
            outputs.build(load(d), run(d) if core_edit else self.model, PIN)
        except GeneratorError as e:
            return sorted({x.code for x in e.diagnostics}), [str(x) for x in e.diagnostics]
        finally:
            shutil.rmtree(d, ignore_errors=True)
        return [], []

    def test_the_example_policy_is_accepted(self):
        self.assertEqual(self._codes(lambda t: t), ([], []))

    def test_an_output_missing_from_the_order_is_g9(self):
        codes, msgs = self._codes(lambda t: t.replace('"c_give_up", ', ""))
        self.assertEqual(codes, ["G9"], msgs)
        self.assertTrue(any("c_give_up" in m for m in msgs), msgs)

    def test_a_missing_selector_is_g9(self):
        codes, msgs = self._codes(lambda t: t.replace('[select.c_start]\nj = { term = "least(Q)", when = "Q != {}" }', ""))
        self.assertEqual(codes, ["G9"], msgs)

    def test_a_selector_for_a_variable_the_summand_does_not_have_is_g9(self):
        codes, msgs = self._codes(lambda t: t + '\n[select."c_finished"]\nj = { term = "job(0)", when = "true" }\n')
        self.assertEqual(codes, ["G9"], msgs)

    def test_a_membership_read_at_another_id_is_g9(self):
        codes, msgs = self._codes(lambda t: t.replace('member = "st(j) == FAILED && tries(j) >= retries"',
                                                      'member = "st(job(jx(j) + 1)) == FAILED && tries(j) >= retries"'))
        self.assertEqual(codes, ["G9"], msgs)

    def test_a_map_without_the_prefix_is_g9(self):
        codes, msgs = self._codes(lambda t: t.replace('order = ["c_give_up"', 'maps = """\nmap qfirst: Nat;\neqn qfirst = 0;\n"""\n\norder = ["c_give_up"'))
        self.assertIn("G9", codes, msgs)

    def test_a_name_starting_with_ix_is_g9(self):
        codes, msgs = self._codes(lambda t: t, lambda c: c.replace("spent", "ix_spent"))
        self.assertIn("G9", codes, msgs)

    def test_an_oldest_index_written_at_two_ids_is_g13(self):
        codes, msgs = self._codes(lambda t: t, lambda c: c.replace("c_finished(j) . Core(st = st[j -> DONE],",
                                                                   "c_finished(j) . Core(st = st[j -> DONE][job(0) -> st(job(0))],"))
        self.assertEqual(codes, ["G13"], msgs)

    def test_a_policy_that_blocks_where_the_model_acts_is_refused(self):
        """c_start always choosing job 0: once it has started, the refined Core waits
        where the model starts the next queued job; the probe system shows it (§3.9.3)."""
        from naima_codegen import systems
        from naima_codegen.checks import blocked_states, explore, read_aut
        from naima_codegen.policy import read_policy
        from naima_codegen.refine import refine

        core = (EXAMPLE / "model" / "core.mcrl2").read_text()
        env = (EXAMPLE / "model" / "env.mcrl2").read_text()
        two = (EXAMPLE / "model" / "instances" / "two.mcrl2").read_text()
        text = (EXAMPLE / "model" / "policy.toml").read_text()
        bad = text.replace('[select.c_start]\nj = { term = "least(Q)", when = "Q != {}" }',
                           '[select.c_start]\nj = { term = "job(0)", when = "Q != {}" }')
        self.assertNotEqual(bad, text)
        ids = systems.IdBounds(self.model.config.id_sorts, _ids_conf())
        counts = []
        with tempfile.TemporaryDirectory() as d:
            for k, t in enumerate((text, bad)):
                r = refine(core, read_policy(t)[0])
                src = Path(d) / ("p%d.mcrl2" % k)
                src.write_text(systems.assemble(r.probe, env, [("two", two)], ids, init_suffix=r.init_suffix,
                                                allow=["probe_enabled", "probe_quiet", "probe_ambiguous"]
                                                + ["probe_deferred_%d" % j for j in range(len(r.deferred))]
                                                + sorted({"%s|%s" % (v, m) for v, m, _ in r.synced})))
                n, edges = read_aut(explore(src, Path(d)))
                counts.append(len(blocked_states(n, edges)))
        self.assertEqual(counts[0], 0)
        self.assertGreater(counts[1], 0)


def _run_rust_sequences(model, types, m, seqs, package=PACKAGE, crate=GEN / "rust" / PACKAGE) -> list:
    """Each sequence through the crate: lines as CrossCheck._python writes them (test_codegen_typescript.py)."""
    from naima_codegen import rustback

    cfg_params = [p for p in model.params if p.role == "config"]
    body = ["use %s::*;" % package, "", "fn hex(b: &[u8]) -> String {",
            '    b.iter().map(|x| format!("{:02x}", x)).collect()', "}", "",
            "fn unhex(s: &str) -> Vec<u8> {",
            "    (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap()).collect()", "}", "",
            "#[allow(clippy::all)]", "fn main() {", "    let text = std::fs::read_to_string(std::env::args().nth(1).unwrap()).unwrap();",
            "    let mut st: Option<State> = None;", "    let mut dead = false;", "    for line in text.lines() {",
            "        let f: Vec<&str> = line.split(' ').collect();", '        if f[0] == "C" {', "            dead = false;"]
    lits = []
    for j, p in enumerate(cfg_params):
        srt = types.resolve(p.sort)
        k = types.kind(srt)
        v = "f[%d]" % (j + 1)
        if k == "bool":
            lits.append("%s: %s == \"1\"" % (rustback.ident(p.name), v))
        elif k == "int":
            lits.append("%s: %s.parse::<i128>().unwrap()" % (rustback.ident(p.name), v))
        else:
            tn = rustback.type_name(srt[1])
            arms = " ".join("%d => %s::%s," % (i, tn, rustback.ident(c)) for i, (c, _, _) in enumerate(types.lps.structs[srt[1]].constructors))
            lits.append("%s: match %s.parse::<u32>().unwrap() { %s _ => panic!() }" % (rustback.ident(p.name), v, arms))
    body += ["            let cfg = Config { %s };" % ", ".join(lits),
             "            let mut out = Vec::new();",
             "            let s = State::new(cfg, &mut out).unwrap();",
             '            let acts: Vec<String> = out.iter().map(|a| { let mut b = Vec::new(); encode(&Entry::Action(a.clone()), &mut b); hex(&b) }).collect();',
             '            println!("init {} {}", acts.join(","), hex(&s.state_bytes()));',
             "            st = Some(s);", '        } else if f[0] == "E" {', "            if dead {", "                continue;", "            }",
             "            let Ok(Entry::Event(ev)) = decode(&unhex(f[1])) else { panic!(\"decode\") };",
             "            let s = st.as_mut().unwrap();", "            let mut out = Vec::new();",
             "            match s.step(&ev, &mut out) {", "                Ok(()) => {",
             '                    let acts: Vec<String> = out.iter().map(|a| { let mut b = Vec::new(); encode(&Entry::Action(a.clone()), &mut b); hex(&b) }).collect();',
             '                    println!("ok {} {}", acts.join(","), hex(&s.state_bytes()));', "                }",
             "                Err(e) => {", "                    let name = match &e {",
             '                        StepError::Refused(_) => "Refused", StepError::Undefined(_) => "Undefined",',
             '                        StepError::Overflow(_) => "Overflow", StepError::StepLimit => "StepLimit",',
             '                        StepError::NotQuiescent => "NotQuiescent",', "                    };",
             '                    println!("err {}", name);', '                    if name != "Refused" {', "                        dead = true;",
             "                    }", "                }", "            }", '        } else if f[0] == "-" {', '            println!("-");', "        }",
             "    }", "}"]
    lines = []
    for config, events in seqs:
        vals = []
        for p in cfg_params:
            v = config[p.name]
            vals.append("1" if v is True else "0" if v is False else str(v))
        lines.append("C %s" % " ".join(vals))
        for ev in events:
            lines.append("E %s" % m.encode((0, ev)).hex())
        lines.append("-")
    d = Path(tempfile.mkdtemp(prefix="naima-codegen-cross-"))
    try:
        (d / "src").mkdir()
        (d / "Cargo.toml").write_text('[package]\nname = "cross"\nversion = "0.0.0"\nedition = "2024"\npublish = false\n\n'
                                      '[dependencies]\n%s = { path = "%s" }\n\n[workspace]\n' % (package, crate))
        (d / "src" / "main.rs").write_text("\n".join(body) + "\n")
        (d / "seq.txt").write_text("\n".join(lines) + "\n")
        shutil.copyfile(ROOT / "rust-toolchain.toml", d / "rust-toolchain.toml")
        target = Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "target" / "codegen-tests"))
        r = subprocess.run(["cargo", "run", "--quiet", "--offline", "--release", "--", str(d / "seq.txt")], cwd=d,
                           capture_output=True, text=True, env=dict(os.environ, CARGO_TARGET_DIR=str(target)))
        if r.returncode:
            raise AssertionError(r.stderr[-4000:])
        out, cur = [], []
        for line in r.stdout.splitlines():
            if line == "-":
                out.append(cur)
                cur = []
            else:
                cur.append(line)
        return out
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
