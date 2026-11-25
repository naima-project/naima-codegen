//! The scheduler of the model-checking example, running five fake tasks on two workers.
//!
//!     cargo run --release --manifest-path examples/scheduler/app/Cargo.toml
//!
//! Options: --workers N (the model's w), --min S and --max S (the seconds a task waits, 3 and 10), --seed N.

mod handlers;

use scheduler_loop::model::{Config, Task};
use scheduler_loop::App;
use std::time::Instant;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let opt = |name: &str, default: f64| {
        args.windows(2).find(|w| w[0] == name).and_then(|w| w[1].parse().ok()).unwrap_or(default)
    };
    let pool = handlers::Pool {
        clock: Instant::now(),
        min: opt("--min", 3.0),
        max: opt("--max", 10.0),
        rng: opt("--seed", 88172645463325252.0) as u64 | 1,
    };
    let app = App::new(Config { w: opt("--workers", 2.0) as i128 }, pool).expect("the initial state");

    // The program: tasks 3 and 4 are the goals; task 3 reads tasks 0 and 1, task 4 reads tasks 2 and 3.
    let ctx = app.context();
    let t = Task;
    ctx.define(t(0), vec![]);
    ctx.define(t(1), vec![]);
    ctx.define(t(2), vec![]);
    ctx.define(t(3), vec![t(0), t(1)]);
    ctx.define(t(4), vec![t(2), t(3)]);
    ctx.goal(t(3));
    ctx.goal(t(4));
    ctx.run();

    app.run().expect("the scheduler refused an event");
}
