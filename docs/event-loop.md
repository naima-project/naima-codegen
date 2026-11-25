# The generic event loop

This page is about the Rust crate that `rust_loop` adds to a project. The stepper that naima-codegen writes is a function
from a state and an event to a new state and a list of actions. It does no input or output and has no clock or threads.
A program that uses it needs a loop around it, and without help every project writes the same loop again by hand. The
generic loop is that loop, written once, and the project writes only what each action does.

## Switching it on

Add `rust_loop = "<crate>"` to the `[project]` table of `codegen.toml`, next to `rust_crate`, and run
`naima-codegen generate`. The project needs the `rust` backend and a policy, because the loop uses the stepper of the
refined process. The generator then writes a second crate under `generated/rust/<crate>/`, which depends on the stepper's
crate by path. The stepper's own files do not change, with or without the key.

[examples/scheduler](../examples/scheduler) is a complete project that does this.

## What is generated

The crate has two source files.

`src/event_loop.rs` is the loop. It is the same text in every project, and a test compares the files of different
models. It defines these items.

| item | role |
| --- | --- |
| `Machine` | A trait for what the loop steps. `step(event, out)` takes an event and appends the actions the model decides. The generated stepper implements it. |
| `Handler` | A trait for what the loop calls for each action. |
| `Ctx<E>` | A handle to post events (`post_event`) and to end the loop (`stop_loop`). It is cloneable and can be sent to other threads. |
| `Loop` | The queue, the machine and the handlers. `run` takes the events one at a time. |

`src/lib.rs` is what the model decides. It is written from the labels of `codegen.toml`, their argument names, and the
Rust types of their sorts.

| item | role |
| --- | --- |
| `Handlers` | A trait with one method per output action, `fn <action>(&mut self, ctx: &Context, <arguments>)`. This is what the project implements. |
| `Context` | The `Ctx` for the model's events, with one method per input event (`fn <event>(&self, <arguments>)`) that posts it. |
| `App` | Made from a `Config` and a value of `Handlers`. `context()` gives a `Context`, and `run()` runs the loop and returns the handlers. |
| `model` | The stepper crate, re-exported, with its sorts, `Config`, `Event` and `Action`. |

## What a project writes

One `impl Handlers` with a method for each output action, and a `main` that makes an `App`, posts the first events and
runs it. In the scheduler example the handlers are in `examples/scheduler/app/src/handlers.rs` and the main in
`examples/scheduler/app/src/main.rs`. A handler that has to wait does so somewhere else, on a thread or a timer, and
reports the outcome by posting an event through the `Context` it was given. In the scheduler, the handler of `start`
spawns a thread that sleeps and then calls `ctx.finished(task)`.

```rust
impl Handlers for Pool {
    fn start(&mut self, ctx: &Context, task: Task) { /* spawn a thread, then ctx.finished(task) */ }
    fn finish(&mut self, ctx: &Context) { ctx.stop_loop(); }
    /* one method per output action */
}
let app = App::new(Config { w: 2 }, Pool::new())?;
app.context().define(Task(0), vec![]);
app.run()?;
```

Changing the actions or events of the program means changing the model, regenerating, and writing the new handlers.
`event_loop.rs` is never edited.

## What the loop guarantees

- Events are taken in the order they were posted, from one unbounded queue.
- For each event the loop steps the machine once and then calls the handler of each returned action, in the order the
  step returned them. All of this happens on the loop's own thread and ends before the next event is taken.
- The actions the machine returned while it was made, in `App::new`, are handled first.
- `stop_loop` ends the loop after the events posted before it have been stepped, and `run` returns the handlers.
  Events posted after the loop has ended are dropped.
- An event the model does not accept ends `run` with the stepper's error. The state is unchanged by a refused event.
- The loop has no unsafe code, and it denies `unwrap`, `expect`, `panic` and indexing in the clippy lints.

## What is verified

The loop is modelled in mCRL2 before it is coded. `tests/loop-model/event-loop.mcrl2` has the program, the inbox, the
handlers and the loop, with the stepper replaced by a table from events to lists of actions. These properties
(`tests/loop-model/*.mcf`) hold.

| file | what it says |
| --- | --- |
| `fifo.mcf` | Events are taken in the order they were posted. |
| `a-step-ends-before-the-next-event.mcf` | All handlers of a step run before the next event is taken. |
| `handlers-in-the-order-of-the-step.mcf` | A step's handlers run in the order of its actions. |
| `nothing-taken-after-the-stop.mcf` | No event is taken after the loop has stopped. |
| `the-events-posted-first-are-taken.mcf` | The events posted before the run start are all taken before it stops. |
| `no-deadlock-before-the-stop.mcf` | Until the loop stops, some action is always possible. |
| `the-loop-can-always-stop.mcf` | From every state before the stop, the loop can reach the stop. |

Replacing the first-in first-out inbox with a last-in first-out one makes `fifo.mcf` fail, so the property is not
vacuous.

The scheduler example goes one step further. `examples/scheduler/model/env.mcrl2` puts the real scheduler together with
the loop, the inbox and the handlers, and the seven properties in `examples/scheduler/model/properties/` hold on that
system. [the scheduler page](examples/scheduler.md) lists them.

What is not verified is the Rust text of `event_loop.rs` against the mCRL2 model of the loop. The two are written to match
and the tests run the scheduler on the generated loop, but there is no proof that the code refines the model.

To check one property by hand, with the mCRL2 tools on `PATH`:

```sh
mcrl22lps -q tests/loop-model/event-loop.mcrl2 loop.lps && lps2lts -q loop.lps loop.lts
lts2pbes -q -f tests/loop-model/fifo.mcf loop.lts p.pbes && pbessolve -q p.pbes
```

`pbessolve` prints `true` when the property holds. `scripts/reproduce.sh` runs all seven.
