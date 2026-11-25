# Tasks that wait on each other

Tasks that start, finish, or wait for another task. It exercises the data structures the generator supports: lists drained from the front, a start order ranked by lists, and a map keyed by records that hold lists. It generates all three backends and has no property of its own.

## Run it

From the root of the repository:

```sh
./naima-codegen check-refinement --project examples/waits
```

On Windows use `naima-codegen.cmd` in place of `./naima-codegen`. The command needs the mCRL2 tools of release 202607.0 on
`PATH`. It explores the model, the refined process (the model with the policy applied) and the probe system, and checks
that the refined process stays inside the model. It ends in a few seconds and prints:

```
small model: 518 states, 1224 transitions (0 s)
small refined: 282 states, 376 transitions (1 s)
small probe: 282 states, 732 transitions (1 s)
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

## The files

```
codegen.toml          the interface declaration and the [project] table
model/core.mcrl2      proc Core, the model
model/policy.toml     the policy
model/codegen-init.mcrl2, env.mcrl2, observers.mcrl2, instances/*.mcrl2
                      the initial state, the environment and the instances the checks run on
generated/            written by naima-codegen generate: the Rust crate, the Python package and the TypeScript modules
```

`./naima-codegen generate --check --project examples/waits` regenerates everything and compares it with `generated/`; it
prints `generated/ is up to date` when the committed code is what the model produces. The page
[docs/examples/waits.md](../../docs/examples/waits.md) describes the model.
