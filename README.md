# naima-codegen

naima-codegen turns a model into code. It reads a process written in mCRL2, in
the subset the generator accepts, and writes the same state machine in the
languages the project chooses: a Rust crate, a Python package, and TypeScript
modules with a Svelte 5 class for user interfaces. The three are peers: each is
checked against mCRL2 itself and the tests compare them with each other. When the model comes with a policy that says
which output to take when several are enabled, it also writes the refined
process and the systems on which a model checker shows that the generated
stepping rules stay inside the model. It is part of the
[naima-project](https://github.com/naima-project), for projects that design
with a model first.

The full specification is [docs/spec.md](docs/spec.md). Sections marked "Not
implemented yet" there describe where the code is behind it.

## What it accepts

The model is one linear process, `proc Core`, whose every summand is
`condition -> c_<label>(args) . Core(assignments)`. Each label is declared an
input (an event the environment sends) or an output (an action Core decides).
The parameters are scalars, columns (functions over an id sort, `sort Job =
struct job(jx: Nat)`), tables (functions over an enumeration), maps, and
frontier sets. User maps are non-recursive equations or walks over lists, which
the generator compiles into loops. Everything outside the subset is refused
with a list of problems, each with a code from G1 to G13 and the section of the
specification it breaks (§3.3 to §3.6, §3.14.1).

The generator needs the mCRL2 toolset at the version the model pins
(`mcrl22lps`, `lpspp`, and for the checks `lps2lts`, `ltscompare`, `lts2pbes`,
`pbessolve`) on `PATH`, and Python 3.11 or later. The tests of the TypeScript
backend also need Deno 2.

## A project

A project is a directory:

```
codegen.toml            the interface declaration and the [project] table
model/
  core.mcrl2            proc Core, no init
  codegen-init.mcrl2    init Core(...), with placeholder configuration values
  policy.toml           optional: the policy (order, selectors, indexes)
  env.mcrl2             with a policy: the environment and proc System
  observers.mcrl2       with a policy: invariant observers, o_* labels
  instances/*.mcrl2     with a policy: the instances the checks run on
properties/             optional: property items, <item>/meta.json
generated/              written by the generator, never by hand
```

`codegen.toml` may also live at `model/codegen.toml`. Its `[project]` table
says where things are and what the generated code is called:

```toml
[project]
backends = ["rust", "python", "typescript"]   # default ["rust", "python"]
rust_crate = "jobqueue"        # with rust: generated/rust/jobqueue/
python_package = "jobqueue"    # with python: generated/python/jobqueue/__init__.py
typescript_module = "jobqueue" # with typescript: generated/typescript/jobqueue/jobqueue.ts and .svelte.ts
# rust_package = "jobqueue"    # the Cargo package name, default rust_crate
# rust_workspace = false       # true: inherit version, edition, lints from the enclosing workspace
# model = "model"
# generated = "generated"
# catalogue = "catalogue"
# properties = "properties"
```

The `[systems]` table groups the instances for the checks and says how id
sorts are bounded in them (§3.17). The rest of the file declares every
parameter's role, every label's kind and argument names, the widths of
integers and the observables (§3.6). With a policy, each summand of Core
follows a tag line, `%@input` or `%@order <level>` (§3.5.4).

Two complete projects, with their generated code committed:
[examples/jobqueue](examples/jobqueue), a job queue with a fixed number of
workers, retries and a give-up rule, and [examples/form](examples/form), a form
that is edited, submitted, sent and answered, made for a user interface.

## TypeScript and Svelte

With `typescript` among the backends, the generator writes two modules
(spec §3.18). `form.ts` is plain TypeScript, with no import and no Svelte:
`step(state, event)` returns the next state and the actions, or `null` when
the model does not allow the event, and `inputs` and `outputs` export the
transition table as data. `form.svelte.ts` wraps it in a class whose state is
`$state` fields behind a read-only `view`, with one method per input label;
the output actions are calls to a port the caller passes in, and what the
port's side effects return comes back as input methods:

```svelte
<script lang="ts">
  import { Form } from "./generated/typescript/form/form.svelte.ts";

  const form = new Form({ limit: 3 }, {
    // c_send, an output of the model: the side effect is here, its outcome an input
    send: (attempt) => api.post(draft, attempt).then(() => form.succeeded(), () => form.failed()),
  });
  let draft = $state("");
</script>

<input bind:value={draft} oninput={() => form.edit(draft.trim() !== "")} />
<button disabled={!form.allowed({ type: "submit" })} onclick={() => form.submit()}>Send</button>
{#if form.view.phase === "SENT"}<p>Sent.</p>{/if}
{#if form.view.phase === "FAILED"}<p>Attempt {form.view.tries} failed.</p>{/if}
```

The component holds no logic: whether the button is enabled, what a click
does and what is shown all come from the model, and a click the model does not
allow changes nothing. The modules import each other with the `.ts`
extension, which Deno and Vite read as they are.

## The command line

```sh
naima-codegen generate [--project DIR]               # write generated/
naima-codegen generate --check [--project DIR]       # regenerate and compare with generated/
naima-codegen generate --front-end [--project DIR]   # only judge the model
naima-codegen check-refinement [--project DIR] [--no-model] [group...]
naima-codegen check-refinement --properties [--project DIR] [group...]
```

`DIR` defaults to the current directory. `python -m naima_codegen` does the
same as the `naima-codegen` script at the root of this repository.
`check-refinement` explores each group's model, refined and probe systems and
checks trace inclusion, non-blocking, that every step ends, that labels select
one summand, that every event is accepted, and that no observer fires (§3.9).
With `--properties` it model-checks the project's property items on the
refined systems instead.

## Tests

From the repository root:

```sh
python3 -m unittest discover -s tests
```

The tests use the `mini` fixture, the refusal cases under
`tests/fixtures/codegen/cases/` and the examples. Most need the pinned mCRL2
toolset and say so when they skip; the ones that build the generated crate need
cargo and the toolchain of `rust-toolchain.toml`; the TypeScript ones need Deno
2, which fetches the pinned `npm:svelte@5.57.2` and `npm:typescript@5.9.3` once
(the drivers are under `tests/ts/`). Each backend is checked against mCRL2's
rewriter and, for the steppers, against the refined Core's state space with
`ltscompare`; the three backends are compared after every event of seeded
random sequences. `tests/fixtures/codegen/make.py`
regenerates the LPS fixtures (`--snapshots` also rewrites the golden snapshots).
Set `CARGO_TARGET_DIR` to share a build directory; the default is
`target/codegen-tests`.

## Licence

MIT: [LICENSE](LICENSE). Code generated by the tool carries no obligation from
this licence.
