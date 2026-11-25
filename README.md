# naima-codegen

naima-codegen turns a model written in mCRL2 into code. You give it one linear process that describes a state machine,
and optionally a policy that says which output to take when several are enabled. It writes a deterministic stepper, a
function from the current state and an event to the new state and the list of actions to carry out. The stepper comes in
Rust, Python and TypeScript (with a Svelte 5 class for user interfaces). For Rust it can also write the loop around the
stepper, a generic event loop that calls one handler per action, so that a program contains only what each action does.

The reason to do this is that the code follows a model that has been checked. The mCRL2 tools can explore the model and
prove properties of it, such as that no deadlock is reachable or that a job never starts twice. naima-codegen writes
the code from the same text, and the generator also writes the refined process, the model with the policy applied, and
the systems on which the model checker shows that the choices the code makes stay inside the model. The tests compare
each backend with mCRL2's own rewriter and with the others.

The tool is written in Python and has no dependencies. It is a command line program, run in place from a clone of this
repository. [LICENSE](LICENSE) is MIT, and the code the tool generates carries no obligation from that licence.

## Install

You need three things. The first two are fetched for you.

| tool | version | what for |
| --- | --- | --- |
| [uv](https://docs.astral.sh/uv/) | any recent release (tested with 0.11) | runs the tool and downloads its Python, 3.13, on first use. No administrator rights are needed. |
| Rust | 1.96.0 | builds the generated Rust crates and the demo. `rustup` reads `rust-toolchain.toml` and installs it. |
| [mCRL2](https://www.mcrl2.org/web/user_manual/download.html) | release 202607.0 | the generator linearises the model with it, and the checks use it. The tools `mcrl22lps`, `lpspp`, `lps2lts`, `ltscompare`, `lts2pbes` and `pbessolve` must be on `PATH`. |

[Deno](https://deno.com) 2 is needed only to run the tests of the TypeScript backend. The versions are pinned in
[versions.env](versions.env), and `tests/test_docs.py` checks that the other files that name a version agree with it.

```sh
git clone https://github.com/naima-project/naima-codegen
cd naima-codegen
./naima-codegen --version          # naima-codegen.cmd on Windows
```

The `naima-codegen` script (and `naima-codegen.cmd` for Windows) starts the tool through uv. If uv is not installed, the
script runs uv's official installer for the current user. To use a Python of your own instead of uv's, set `NAIMA_PYTHON`
to it, or run `python -m naima_codegen` from the root of the clone.

The generator checks the mCRL2 release against the `mcrl2_version` in the project and refuses another one, because the
linearisation it reads can differ between releases.

## Quick start

This takes about five minutes. It uses [examples/scheduler](examples/scheduler), a pool of workers that runs a graph of
tasks. All the commands run from the root of the clone.

1. Check that the code in the example is what its model produces. This regenerates everything and compares it, byte for
   byte, with what is committed.

   ```sh
   ./naima-codegen generate --check --project examples/scheduler
   ```

   It prints `naima-codegen generate --check: generated/ is up to date`.

2. Run the demo, with the waits of its fake tasks shortened to a fraction of a second.

   ```sh
   cargo run --release --manifest-path examples/scheduler/app/Cargo.toml -- --min 0.2 --max 0.6 --seed 1
   ```

   It prints a line when each of five tasks starts and ends, when the two goals are delivered, and
   `the run is over` at the end. [examples/scheduler/README.md](examples/scheduler/README.md) shows the output. Without the
   options the tasks wait three to ten seconds and the run takes about twenty.

3. Check that the stepper stays inside the model, and that the model's properties hold.

   ```sh
   ./naima-codegen check-refinement --project examples/scheduler
   ./naima-codegen check-refinement --properties --project examples/scheduler
   ```

   Each ends with `check-refinement: every check holds`. The second line lists the seven properties and says that each
   one holds.

4. Change something. Edit the policy in `examples/scheduler/model/policy.toml`, for example by swapping two outputs in
   `order`. Run `generate --check` again and it reports which files differ. Run `generate` and the files are rewritten.

To do all of this, and run the test suite, in one go, use [scripts/reproduce.sh](scripts/reproduce.sh) (see below).

## The files a user writes

A project is a directory.

```
codegen.toml            the interface declaration and the [project] table
model/
  core.mcrl2            proc Core, with no init
  codegen-init.mcrl2    init Core(...), with placeholder configuration values
  policy.toml           optional: which output to take when several are enabled
  env.mcrl2             with a policy: the environment of the model, and proc System
  observers.mcrl2       with a policy: observers of invariants
  instances/*.mcrl2     with a policy: the instances the checks run on
properties/             optional: property items, <item>/meta.json
generated/              written by the generator, never by hand
```

`core.mcrl2` is one linear process in which every summand has the form `condition -> c_<label>(args) . Core(assignments)`.
A tag line before each summand says whether it is an input (`%@input`, something the environment sends) or an output
(`%@order <level>`, something the process decides). Parameters may be scalars, columns (functions over an id sort such as
`sort Job = struct job(jx: Nat)`), tables, maps and sets. Whatever is outside this subset is refused with a numbered error
(G1 to G13), each with the section of [docs/spec.md](docs/spec.md) it breaks.

`codegen.toml` says what the parameters are for, which labels are inputs and outputs, and which backends to write. The
keys of `codegen.toml` and `policy.toml` are listed in [docs/reference.md](docs/reference.md).

The generator writes to `generated/`: the code of each backend, the refined process and the systems for the checks in
`generated/model/`, and a `MANIFEST.json` with the hashes of the inputs. Nothing there is edited by hand, and
`generate --check` fails when it has drifted.

To write the loop for the Rust backend too, set `rust_loop` in `[project]` and write the handlers
([docs/event-loop.md](docs/event-loop.md)).

## The command line

```sh
naima-codegen generate [--project DIR]               # write generated/
naima-codegen generate --check [--project DIR]       # regenerate and compare with generated/
naima-codegen generate --front-end [--project DIR]   # only judge the model
naima-codegen check-refinement [--project DIR] [--no-model] [group...]
naima-codegen check-refinement --properties [--project DIR] [group...]
naima-codegen --version
```

`DIR` defaults to the current directory. `check-refinement` explores the model, the refined process and a probe system for
each group of instances. It checks that the refined traces are traces of the model, that no step goes on forever, that no
reachable state is blocked, that every label selects one summand, that every event the environment can send is accepted,
and that no observer fires. With `--properties` it checks the property items of the project on the refined systems.

## Verifying a model with the mCRL2 tools directly

Everything the generator and `check-refinement` do uses the standard mCRL2 tools, and the systems are plain mCRL2 files, so
you can read them and check your own properties without this tool. The refined system of the scheduler example is
`examples/scheduler/generated/model/systems/small-refined.mcrl2`, and its properties are modal mu-calculus formulas in
`examples/scheduler/model/properties/`.

```sh
cd examples/scheduler
mcrl22lps -q generated/model/systems/small-refined.mcrl2 r.lps
lps2lts -q r.lps r.lts
lts2pbes -q -f model/properties/each-task-run-once.mcf r.lts p.pbes
pbessolve -q p.pbes                 # prints true when the property holds
```

Naima, the design workshop this tool was written for, drives the same tools from its tracker, but it is not needed to
use this repository.

## Backends

| backend | what is written | status |
| --- | --- | --- |
| Rust | A crate `generated/rust/<name>/` with the stepper (`State::new`, `State::step`), the events and actions as enums, and a binary encoding of records. It has no unsafe code and no panicking path. With `rust_loop`, a second crate with the `Handlers` trait and the generic event loop. | The most complete. The tests build it with the pinned toolchain. |
| Python | A package `generated/python/<name>/__init__.py` with the same stepper. It imports no I/O, thread or clock module. | Complete for the stepper. Values are generic, so it is not tuned for memory. |
| TypeScript | `<name>.ts`, plain TypeScript with `step(state, event)` and the transition table as data, and `<name>.svelte.ts`, a Svelte 5 class over it. | Complete for the stepper. Integers beyond 2^53 - 1 are reported as an overflow where Rust and Python compute them exactly. |

The three backends are checked against mCRL2's rewriter, against the state space of the refined process (strong
bisimulation, with `ltscompare`), and against each other after every event of seeded random sequences. The generic event
loop exists for Rust only.

## Limits

- The model must be in the accepted subset. This is a deliberate restriction, since the generator has to know the cost of
  each step and the meaning of each sort. Examples of what is refused are real numbers, recursive sorts, sets in action
  arguments, quantifiers over a frontier set and recursive maps that are not simple walks over lists.
- A model has one `proc Core` and its environment is not generated. The environment is whatever calls the stepper, and the
  checks use a model of it that you write in `env.mcrl2`.
- The `observable` declarations are read and checked, and no backend writes getters for them yet.
- The cost analysis of a summand (the check that a step does not grow with the number of ids) and the `repr` choices of the
  data structures are only partly implemented. The generated code is correct but not tuned in memory or time to the
  costs the specification describes.
- The Python binding of the Rust crate and recorded-run replay are not implemented.
- The mCRL2 release is pinned per model. The generator refuses another one.
- Of the loop around the stepper, only the Rust one exists, and the Rust text of the loop is verified through its mCRL2
  model, not by a proof that the code refines it.

[docs/spec.md](docs/spec.md) is the full specification, and each place where the code is behind it is marked
"Not implemented yet".

## Reproducing the results

One script goes from a fresh clone to a report of what holds.

```sh
scripts/reproduce.sh                 # Linux and macOS
powershell -File scripts\reproduce.ps1    # Windows (scripts\reproduce.cmd calls it)
```

It installs the pinned Python through uv and the pinned Rust toolchain through rustup, regenerates every example and checks
it byte for byte against what is committed, runs the test suite, runs the scheduler demo with short waits, and, when the
pinned mCRL2 tools are on `PATH`, runs the refinement checks and the properties of every example and of the event loop's
model. A step whose tool is missing is skipped, and the summary at the end says what was skipped and why. The script
exits with a failure if any step that ran failed. `--no-tests` leaves out the test suite.

## Tests

```sh
uv run --frozen --no-dev python -B -m unittest discover -s tests
```

The tests use a small fixture, the refusal cases under `tests/fixtures/codegen/cases/` and the examples. Those that need
mCRL2, cargo or Deno say so and skip when it is missing. The TypeScript tests fetch the pinned `npm:svelte@5.57.2` and
`npm:typescript@5.9.3` on first use. `tests/fixtures/codegen/make.py` regenerates the LPS fixtures (`--snapshots` also
rewrites the golden snapshots). Set `CARGO_TARGET_DIR` to share a build directory; the default is `target/codegen-tests`.

## More

- [docs/reference.md](docs/reference.md): every key of `codegen.toml` and `policy.toml`.
- [docs/event-loop.md](docs/event-loop.md): the `Handlers` trait, the `Context`, and what the loop guarantees.
- [docs/examples/](docs/examples): one page per example. The examples are [form](examples/form),
  [jobqueue](examples/jobqueue), [waits](examples/waits) and [scheduler](examples/scheduler), each with a README that has
  a command to run it and its expected output.
- [docs/spec.md](docs/spec.md): the specification.
- [CHANGELOG.md](CHANGELOG.md): what changed.
