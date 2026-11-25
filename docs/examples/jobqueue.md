# Example: the job queue

[examples/jobqueue](../../examples/jobqueue) is a queue of jobs run by a fixed number of workers. It shows an id sort
(jobs), columns indexed by that sort, a policy with indexes, and all three backends.

## The model

Clients submit jobs, numbered in submission order. A queued job starts when a worker slot is free. A running job
finishes or crashes. A crashed job is queued again while it has tries left, and it is given up after the limit of tries.

| label | kind | meaning |
| --- | --- | --- |
| `c_submit(job)` | input | A client submits a job. |
| `c_finished(job)`, `c_crashed(job)` | input | A worker reports the outcome of a job. |
| `c_start(job)` | output | The queue gives a job to a free worker. |
| `c_retry(job)` | output | A crashed job with tries left goes back to the queue. |
| `c_give_up(job)` | output | A crashed job with no try left is abandoned. |

`Job` is the id sort. The state has columns for the status and the number of tries of each job, and a set for the queue.
`w`, the number of workers, is configuration, and `retries` is a constant.

## The policy

Several outputs can be enabled at once, and the policy picks one. `order` ranks `c_give_up` before `c_retry` before
`c_start`, so a crashed job is dealt with before a new one starts. The indexes give the job within an output:

- `c_start` takes `least(Q)`, the queued job with the lowest number, so the queue is served in submission order.
- `c_retry` takes the first of the index `retryable`, the crashed jobs with tries left, oldest crash first.
- `c_give_up` takes the first of `spent`, the crashed jobs with no try left, lowest number first.

## What is checked

The instances `one`, `two` and `three` (that many jobs) form the group `small`, and `flaky` (one worker and one job that
crashes on every try) is checked on its own. The checks show that the refined process stays inside the model. The
property `no-double-start.mcf` says that a job is not started again while it is at a worker, that is, after `start(j)`
the next `start(j)` comes only after `finished(j)` or `crashed(j)`. It holds.
