# The pool scheduler on the generic event loop

A pool scheduler, a standard example for model checking: a fixed number of workers runs a graph of tasks, each of which reads the results of earlier ones. Here it runs for
real, on the event loop that naima-codegen generates, with fake tasks: each task prints when it starts and when it ends, and waits a random
3 to 10 seconds in between.

## Run it

From the root of the repository, with Rust 1.96.0 installed (`rustup` reads `rust-toolchain.toml` and fetches it):

```sh
cargo run --release --manifest-path examples/scheduler/app/Cargo.toml
```

The program runs five tasks on two workers (tasks 0, 1 and 2 read nothing, task 3 reads 0 and 1, task 4 reads 2 and 3; 3 and 4
are goals). Each task waits a random 3 to 10 seconds, so a run takes about 20 seconds. It prints, for instance:

```
[  0.00s] task 0 starts
[  0.00s] task 1 starts
[  4.41s] task 1 ends
[  4.41s] task 2 starts
...
[ 21.07s] goal 4 delivered, done
[ 21.07s] the run is over
```

For a run of about a second, shorten the waits (the times and the order of tasks that end close together vary from run to run):

```sh
cargo run --release --manifest-path examples/scheduler/app/Cargo.toml -- --min 0.2 --max 0.6 --seed 1
```

```
[  0.00s] task 0 starts
[  0.00s] task 1 starts
[  0.20s] task 0 ends
[  0.20s] task 2 starts
[  0.23s] task 1 ends
[  0.23s] task 3 starts
[  0.64s] task 2 ends
[  0.81s] task 3 ends
[  0.81s] goal 3 delivered, done
[  0.81s] task 4 starts
[  1.22s] task 4 ends
[  1.22s] goal 4 delivered, done
[  1.22s] the run is over
```

Options after `--`: `--workers N` (default 2), `--min S` and `--max S` (the wait in seconds, 3 and 10), `--seed N`.
It needs only Rust, because the generated code is committed. The page [docs/examples/scheduler.md](../../docs/examples/scheduler.md)
describes the model and [docs/event-loop.md](../../docs/event-loop.md) the loop.

## The files a user writes

```
app/Cargo.toml        the package, depending on generated/rust/scheduler_loop
app/src/handlers.rs   what each of the model's six output actions does: start a fake task on a thread, print a goal, stop
app/src/main.rs       the program: posts the tasks, the goals and the run through the context, and runs the loop
```

`handlers.rs` is one `impl Handlers for Pool` with a method for each output action of the model (`start`, `ready`, `tell`,
`abandon`, `deliver`, `finish`). `main.rs` makes an `App`, posts `define`, `goal` and `run` through its `Context`, and calls `run`.
Neither contains the event loop or the dispatch of an action: `generated/rust/scheduler_loop/src/event_loop.rs` is the same file in
every project. To use other actions and events, change `model/core.mcrl2` and `codegen.toml`, run `naima-codegen generate`, and write the new
handlers.

## The model and what is checked

`model/core.mcrl2`, `model/policy.toml` and `codegen.toml` describe the scheduler. `model/env.mcrl2` is its environment as this program
has it: the program posts to an inbox (a first-in first-out queue), the loop takes one event at a time and hands it to the scheduler, the
handler of `start` lets a thread end the task and post `finished`, and the handler of `finish` stops the loop. The properties in
`model/properties/`, on the refined system `generated/model/systems/small-refined.mcrl2` (485 states, 725 transitions), checked with mCRL2
202607.0, all hold:

| property | file |
| --- | --- |
| no deadlock before the run is over | `no-deadlock.mcf` |
| every goal is delivered | `every-goal-delivered.mcf` |
| never more tasks running than workers | `never-over-workers.mcf` |
| never more threads than workers | `threads-within-workers.mcf` |
| each task is run once | `each-task-run-once.mcf` |
| no task starts before the tasks it reads have finished | `no-task-before-inputs.mcf` |
| nothing is posted after the run is over | `nothing-after-the-end.mcf` |

## Verified

The loop itself is modelled in `tests/loop-model/event-loop.mcrl2` over an abstract stepper and checked with mCRL2 202607.0; these
properties hold (`tests/loop-model/*.mcf`): `fifo` (events are taken in the order they were posted), `a-step-ends-before-the-next-event`,
`handlers-in-the-order-of-the-step`, `nothing-taken-after-the-stop`, `the-events-posted-first-are-taken`, `no-deadlock-before-the-stop`
and `the-loop-can-always-stop`. A last-in first-out inbox makes `fifo` fail.

The scheduler composed with the loop, the inbox and the handlers (`model/env.mcrl2`) satisfies the seven properties of the table above, and
`naima-codegen check-refinement --project examples/scheduler` (trace inclusion, every step ends, non-blocking, one summand per label,
receptive) and its `--properties` form both report that every check holds. `tests/test_codegen_loop.py` runs all of these.

Check one by hand, with the mCRL2 tools on `PATH`:

```sh
mcrl22lps -q generated/model/systems/small-refined.mcrl2 r.lps && lps2lts -q r.lps r.lts
lts2pbes -q -f model/properties/each-task-run-once.mcf r.lts p.pbes && pbessolve -q p.pbes
```

Regenerate after a change of the model with `./naima-codegen generate --project examples/scheduler`.
