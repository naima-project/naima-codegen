# Example: tasks that wait on each other

[examples/waits](../../examples/waits) is the example with the richest data structures. It exists to exercise the
generator on lists that are drained from the front, a start order ranked by lists, and a map keyed by records that hold
lists. It generates all three backends and has no property file of its own.

## The model

A client opens tasks, numbered in the order they are opened, each with a label from 0 to 3. A queued task starts at once. A
running task finishes, or needs another live task and waits until that one is done. A done task tells its waiters one at
a time, the oldest first, and a waiter whose needs are all told is queued again. Queued tasks start in the order of their
rank, which is a list of numbers. A done task that nobody waits on is dropped, and its label can then be opened again.

| label | kind | meaning |
| --- | --- | --- |
| `c_open(label)` | input | A task is opened. |
| `c_need(task, other)` | input | A running task needs another one. |
| `c_finish(task)`, `c_finish2(task, other)` | input | One task, or two at once, finish. |
| `c_tell(task, waiter)` | output | A done task tells one of its waiters. |
| `c_drop(task)` | output | A done task with no waiters is dropped. |
| `c_start(task)` | output | A queued task starts. |

## The policy

`order` puts telling first, so a done task's waiters are queued again before anything starts, and dropping before a
start, so a label is free before a start can need it. The three indexes are `telling` (done tasks with waiters, ordered
by when they became done, oldest first), `droppable` (done tasks nobody waits on, newest first) and `queue` (queued
tasks, ordered by the key `rank`, a list, compared as mCRL2 compares lists: `[0]` before `[0, 2]` before `[1]`).

## What is checked

The instances `two` and `three` form the group `small`. `check-refinement` finds 518 states in the model and 282 in the
refined process, and all the checks hold. The generated code of the three backends is compared in the tests, event by
event, on seeded random sequences.
