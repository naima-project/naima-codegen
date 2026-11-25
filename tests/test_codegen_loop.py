"""The generic event loop (docs/spec.md §3.19).

- the loop is one constant text: the same file for the form, the job queue and the scheduler;
- the option `rust_loop` only adds a crate: the stepper's files are the same with and without it;
- a project's model is swapped for another, the project is regenerated, and a program of only new
  handlers runs on the same loop;
- the scheduler example, on the generic loop with fake tasks, run with small waits.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from naima_codegen import outputs  # noqa: E402
from naima_codegen.frontend import run  # noqa: E402
from naima_codegen.project import load  # noqa: E402
from naima_codegen.toolchain import Toolchain  # noqa: E402

PIN = "202607.0"
CARGO = shutil.which("cargo")
needs_tools = unittest.skipUnless(Toolchain().version() == PIN, "the pinned mCRL2 toolset is not installed")
needs_cargo = unittest.skipUnless(CARGO, "cargo is not installed")


def _files(project: Path) -> dict:
    proj = load(project)
    return outputs.build(proj, run(proj), Toolchain().version())


def _app_project(dst: Path, example: str, with_loop: bool = True) -> Path:
    """A project directory made from an example, for the Rust backend only, its stepper crate called `model`
    and its handler crate `app_loop`: the names a hand-written program depends on do not change with the model."""
    src = ROOT / "examples" / example
    shutil.rmtree(dst / "model", ignore_errors=True)
    shutil.copytree(src / "model", dst / "model")
    toml = (src / "codegen.toml").read_text()
    toml = re.sub(r"^\[project\]\n(?:.*\n)*?(?=\n|\[)", "", toml, count=1, flags=re.M)
    head = 'backends = ["rust"]\nrust_crate = "model"\n' + ('rust_loop = "app_loop"\n' if with_loop else "")
    (dst / "codegen.toml").write_text(toml.replace("[widths]", "[project]\n" + head + "\n[widths]", 1))
    return dst


def _cargo(app: Path, *args: str, target: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, CARGO_TARGET_DIR=str(target))
    return subprocess.run([CARGO, *args], cwd=app, capture_output=True, text=True, env=env, timeout=900)


FORM_MAIN = '''
use app_loop::{model, App, Context, Handlers};
struct H;
impl Handlers for H {
    fn send(&mut self, ctx: &Context, attempt: i128) {
        println!("send {attempt}");
        ctx.succeeded();
        ctx.stop_loop();
    }
}
fn main() {
    let app = App::new(model::Config { limit: 3 }, H).expect("start");
    let ctx = app.context();
    ctx.edit(true);
    ctx.submit();
    app.run().expect("run");
}
'''

# The same project, its model swapped for the job queue's: other events, other actions, another sort.
JOBS_MAIN = '''
use app_loop::{model::{Config, Job}, App, Context, Handlers};
struct H;
impl Handlers for H {
    fn start(&mut self, ctx: &Context, job: Job) {
        println!("start {}", job.0);
        ctx.crashed(job);
    }
    fn retry(&mut self, _ctx: &Context, job: Job) {
        println!("retry {}", job.0);
    }
    fn give_up(&mut self, ctx: &Context, job: Job) {
        println!("give up {}", job.0);
        ctx.stop_loop();
    }
}
fn main() {
    let app = App::new(Config { w: 1 }, H).expect("start");
    app.context().submit(Job(0));
    app.run().expect("run");
}
'''


def _write_app(project: Path, main: str) -> Path:
    app = project / "app"
    (app / "src").mkdir(parents=True, exist_ok=True)
    (app / "Cargo.toml").write_text(
        '[package]\nname = "app"\nversion = "0.0.0"\nedition = "2024"\n\n'
        '[dependencies]\napp_loop = { path = "../generated/rust/app_loop" }\n\n[workspace]\n')
    (app / "src" / "main.rs").write_text(main)
    return app


@needs_tools
class Loop(unittest.TestCase):
    def test_the_loop_is_one_constant_text(self):
        texts = set()
        with tempfile.TemporaryDirectory() as d:
            for ex in ("form", "jobqueue"):
                (Path(d) / ex).mkdir()
                texts.add(_files(_app_project(Path(d) / ex, ex))["rust/app_loop/src/event_loop.rs"])
        texts.add((ROOT / "examples" / "scheduler" / "generated" / "rust" / "scheduler_loop" / "src" / "event_loop.rs").read_text())
        self.assertEqual(len(texts), 1)

    def test_the_stepper_is_the_same_with_and_without_the_loop(self):
        with tempfile.TemporaryDirectory() as d:
            plain = _files(_app_project(Path(d), "jobqueue", with_loop=False))
            withloop = _files(_app_project(Path(d), "jobqueue"))
        extra = {k for k in withloop if k not in plain}
        self.assertEqual(extra, {"rust/app_loop/Cargo.toml", "rust/app_loop/src/lib.rs", "rust/app_loop/src/event_loop.rs"})
        for k, v in plain.items():
            if k != "MANIFEST.json":
                self.assertEqual(withloop[k], v, k)

    def test_the_scheduler_example_is_up_to_date(self):
        proj = load(ROOT / "examples" / "scheduler")
        self.assertIsNone(outputs.diff(proj, outputs.build(proj, run(proj), Toolchain().version())))

    @needs_cargo
    def test_swapping_the_model_needs_only_new_handlers(self):
        with tempfile.TemporaryDirectory() as d:
            project, target = Path(d) / "p", Path(d) / "target"
            project.mkdir()
            loop = {}
            for example, main, expected in (("form", FORM_MAIN, "send 1\n"),
                                            ("jobqueue", JOBS_MAIN, "start 0\nretry 0\nstart 0\ngive up 0\n")):
                _app_project(project, example)
                self.assertEqual(subprocess.run([sys.executable, "-m", "naima_codegen", "generate", "--project", str(project)],
                                                cwd=ROOT, capture_output=True, text=True).returncode, 0)
                loop[example] = (project / "generated/rust/app_loop/src/event_loop.rs").read_text()
                app = _write_app(project, main)
                r = _cargo(app, "run", "--quiet", target=target)
                self.assertEqual((r.returncode, r.stdout), (0, expected), r.stderr[-2000:])
            self.assertEqual(loop["form"], loop["jobqueue"])


def _holds(model: Path, formula: Path, work: Path) -> bool:
    """Whether the mu-calculus formula holds on the model's state space (mCRL2: lps, lts, pbes, solve)."""
    def tool(name):
        from naima_codegen.checks import tool as find
        return find(name)

    def sh(*cmd):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        assert r.returncode == 0, (cmd[0], r.stderr[-500:])
        return r.stdout
    lps, lts, pbes = work / (model.stem + ".lps"), work / (model.stem + ".lts"), work / "p.pbes"
    if not lts.exists():
        sh(tool("mcrl22lps"), "-q", str(model), str(lps))
        sh(tool("lps2lts"), "-q", str(lps), str(lts))
    sh(tool("lts2pbes"), "-q", "-f", str(formula), str(lts), str(pbes))
    return sh(tool("pbessolve"), "-q", str(pbes)).strip().endswith("true")


@needs_tools
class Models(unittest.TestCase):
    """The loop's model, and the scheduler's with the loop and the handlers in its environment, checked with mCRL2."""

    def _all(self, model: Path, formulas: list):
        with tempfile.TemporaryDirectory() as d:
            for f in formulas:
                with self.subTest(f.stem):
                    self.assertTrue(_holds(model, f, Path(d)))

    def test_the_generic_loop(self):
        d = ROOT / "tests" / "loop-model"
        self._all(d / "event-loop.mcrl2", sorted(d.glob("*.mcf")))

    def test_the_scheduler_on_the_loop(self):
        d = ROOT / "examples" / "scheduler"
        self._all(d / "generated" / "model" / "systems" / "small-refined.mcrl2", sorted((d / "model" / "properties").glob("*.mcf")))


@needs_cargo
class Scheduler(unittest.TestCase):
    """The scheduler of the model-checking example, with fake tasks that wait 0.05 to 0.2 seconds."""

    def test_the_run(self):
        app = ROOT / "examples" / "scheduler" / "app"
        with tempfile.TemporaryDirectory() as d:
            r = _cargo(app, "run", "--release", "--quiet", "--", "--min", "0.05", "--max", "0.2", "--seed", "7",
                       target=Path(d))
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        lines = [re.sub(r"^\[\s*[\d.]+s\] ", "", x) for x in r.stdout.splitlines()]
        starts = [int(m.group(1)) for x in lines if (m := re.fullmatch(r"task (\d) starts", x))]
        ends = [int(m.group(1)) for x in lines if (m := re.fullmatch(r"task (\d) ends", x))]
        self.assertEqual(sorted(starts), [0, 1, 2, 3, 4])  # each task runs once
        self.assertEqual(sorted(ends), [0, 1, 2, 3, 4])
        running, most = 0, 0
        for x in lines:
            if re.fullmatch(r"task \d starts", x):
                running += 1
                most = max(most, running)
            elif re.fullmatch(r"task \d ends", x):
                running -= 1
        self.assertLessEqual(most, 2)  # at most the two workers
        for t, reads in ((3, (0, 1)), (4, (2, 3))):  # no task before the tasks it reads have ended
            for k in reads:
                self.assertLess(lines.index("task %d ends" % k), lines.index("task %d starts" % t))
        self.assertEqual([x for x in lines if x.startswith("goal")], ["goal 3 delivered, done", "goal 4 delivered, done"])
        self.assertEqual(lines[-1], "the run is over")


if __name__ == "__main__":
    unittest.main()
