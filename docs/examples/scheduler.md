# Example: the pool scheduler

[examples/scheduler](../../examples/scheduler) is a pool of workers running a graph of tasks. It is the example for the
generic event loop, with a real program of fake tasks on top. It generates the Rust backend only.

## The model

Before the run, the environment gives the tasks, each with the earlier tasks it reads, and marks some as goals. It then
starts the run. From then on the scheduler marks a task ready once everything it reads is done, gives the oldest ready
task to a free worker, and delivers each goal once it is done or has failed. The environment reports every task that runs
as finished or failed, and a task that reads a failed task fails without running.

| label | kind | meaning |
| --- | --- | --- |
| `c_define(task, reads)` | input | A task and the tasks it reads. |
| `c_goal(task)` | input | The task is a goal. |
| `c_run` | input | The program is complete, start. |
| `c_finished(task)`, `c_failed(task)` | input | A running task ended. |
| `c_ready(task)` | output | All the inputs of a waiting task are done. |
| `c_start(task)` | output | A ready task goes to a free worker. |
| `c_abandon(task)` | output | A waiting task that reads a failed task fails without running. |
| `c_tell(from, to)` | output | A settled task tells one that reads it. |
| `c_deliver(task, ok)` | output | A goal that is done or failed is delivered. |
| `c_finish` | output | The run is over. |

## The policy

`order` is `c_deliver`, `c_tell`, `c_abandon`, `c_ready`, `c_start`, `c_finish`: deliver what is finished first, then let
settled tasks tell those that read them, settle tasks whose inputs failed, mark tasks ready, give ready tasks to free
workers, and end the run last. Each of the first five outputs takes the first member of an index, ordered by task number.

## What is checked

On the refined system for five tasks (`generated/model/systems/small-refined.mcrl2`, 485 states and 725 transitions),
seven properties hold. They are checked on `model/env.mcrl2`, which composes the scheduler with the event loop, an
inbox, and the handlers of the demo program.

| file in `model/properties/` | what it says |
| --- | --- |
| `no-deadlock.mcf` | No deadlock before the run is over. |
| `every-goal-delivered.mcf` | Every goal is delivered. |
| `never-over-workers.mcf` | Never more tasks running than workers. |
| `threads-within-workers.mcf` | Never more threads than workers. |
| `each-task-run-once.mcf` | Each task is run once. |
| `no-task-before-inputs.mcf` | No task starts before the tasks it reads have finished. |
| `nothing-after-the-end.mcf` | Nothing is posted after the run is over. |

The README of the example has the commands, and [event-loop.md](../event-loop.md) describes the loop.
