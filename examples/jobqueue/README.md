# The job queue

A queue of jobs run by a fixed number of workers, with retries for jobs that crash and a limit on the tries. It generates all three backends and has one property, that a job is never started twice while it is at a worker.

## Run it

From the root of the repository:

```sh
./naima-codegen check-refinement --project examples/jobqueue
```

On Windows use `naima-codegen.cmd` in place of `./naima-codegen`. The command needs the mCRL2 tools of release 202607.0 on
`PATH`. It explores the model, the refined process (the model with the policy applied) and the probe system, and checks
that the refined process stays inside the model. It ends in a few seconds and prints:

```
flaky model: 9 states, 8 transitions (0 s)
flaky refined: 9 states, 8 transitions (0 s)
flaky probe: 9 states, 16 transitions (1 s)
flaky observers on the refined system: none
flaky §3.9.2 trace inclusion: holds
flaky §3.9.5 every step ends: holds
flaky §3.9.3 non-blocking: holds
flaky §3.9.3 deferred (states, reported): {}
flaky §3.9.6 one summand per label: holds
flaky §3.9.7 receptive: holds
small model: 102 states, 184 transitions (0 s)
small refined: 54 states, 67 transitions (0 s)
small probe: 54 states, 122 transitions (1 s)
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
./naima-codegen check-refinement --properties --project examples/jobqueue
```

It prints `holds` for `no-double-start.mcf` and ends with `check-refinement: every check holds`.

## The files

```
codegen.toml          the interface declaration and the [project] table
model/core.mcrl2      proc Core, the model
model/policy.toml     the policy
model/codegen-init.mcrl2, env.mcrl2, observers.mcrl2, instances/*.mcrl2
                      the initial state, the environment and the instances the checks run on
generated/            written by naima-codegen generate: the Rust crate, the Python package and the TypeScript modules
```

`./naima-codegen generate --check --project examples/jobqueue` regenerates everything and compares it with `generated/`; it
prints `generated/ is up to date` when the committed code is what the model produces. The page
[docs/examples/jobqueue.md](../../docs/examples/jobqueue.md) describes the model.
