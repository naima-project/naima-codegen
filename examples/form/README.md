# The form

A form that is edited, submitted, sent and answered. The model is the state machine behind a user interface, and the example generates all three backends, including the TypeScript and Svelte modules.

## Run it

From the root of the repository:

```sh
./naima-codegen check-refinement --project examples/form
```

On Windows use `naima-codegen.cmd` in place of `./naima-codegen`. The command needs the mCRL2 tools of release 202607.0 on
`PATH`. It explores the model, the refined process (the model with the policy applied) and the probe system, and checks
that the refined process stays inside the model. It ends in a few seconds and prints:

```
small model: 23 states, 42 transitions (0 s)
small refined: 23 states, 42 transitions (0 s)
small probe: 23 states, 64 transitions (0 s)
small observers on the refined system: none
small §3.9.2 trace inclusion: holds
small §3.9.5 every step ends: holds
small §3.9.3 non-blocking: holds
small §3.9.3 deferred (states, reported): {}
small §3.9.6 one summand per label: holds
small §3.9.7 receptive: holds
check-refinement: every check holds
```

The state counts and the checks are the same on every run. The times in parentheses differ.

To check the property of the project (the file under `model/properties/`) on the refined system as well:

```sh
./naima-codegen check-refinement --properties --project examples/form
```

It prints `holds` for `one-send-per-submit.mcf` and ends with `check-refinement: every check holds`.

## The files

```
codegen.toml          the interface declaration and the [project] table
model/core.mcrl2      proc Core, the model
model/policy.toml     the policy
model/codegen-init.mcrl2, env.mcrl2, observers.mcrl2, instances/*.mcrl2
                      the initial state, the environment and the instances the checks run on
generated/            written by naima-codegen generate: the Rust crate, the Python package and the TypeScript modules
```

`./naima-codegen generate --check --project examples/form` regenerates everything and compares it with `generated/`; it
prints `generated/ is up to date` when the committed code is what the model produces. The page
[docs/examples/form.md](../../docs/examples/form.md) describes the model.
