//! What each action of the scheduler's model does here. The model decides when; this decides how.
//! The tasks are fake: a task prints when it starts and when it ends, and in between sleeps a random time.

use scheduler_loop::model::Task;
use scheduler_loop::{Context, Handlers};
use std::time::{Duration, Instant};

pub struct Pool {
    pub clock: Instant,
    pub min: f64,
    pub max: f64,
    pub rng: u64,
}

impl Pool {
    /// A random wait between `min` and `max` seconds.
    fn wait(&mut self) -> Duration {
        self.rng ^= self.rng << 13;
        self.rng ^= self.rng >> 7;
        self.rng ^= self.rng << 17;
        let unit = (self.rng >> 11) as f64 / (1u64 << 53) as f64;
        Duration::from_secs_f64(self.min + unit * (self.max - self.min))
    }

    fn say(&self, what: String) {
        println!("[{:6.2}s] {what}", self.clock.elapsed().as_secs_f64());
    }
}

impl Handlers for Pool {
    // A worker is free and the task is next in line: run it on a thread, and tell the model when it ends.
    fn start(&mut self, ctx: &Context, task: Task) {
        let wait = self.wait();
        let (ctx, clock) = (ctx.clone(), self.clock);
        self.say(format!("task {} starts", task.0));
        std::thread::spawn(move || {
            std::thread::sleep(wait);
            println!("[{:6.2}s] task {} ends", clock.elapsed().as_secs_f64(), task.0);
            ctx.finished(task);
        });
    }

    fn deliver(&mut self, _ctx: &Context, task: Task, ok: bool) {
        self.say(format!("goal {} delivered, {}", task.0, if ok { "done" } else { "failed" }));
    }

    fn finish(&mut self, ctx: &Context) {
        self.say("the run is over".to_string());
        ctx.stop_loop();
    }

    // The scheduler's bookkeeping needs nothing from the outside.
    fn ready(&mut self, _ctx: &Context, _task: Task) {}
    fn tell(&mut self, _ctx: &Context, _from: Task, _to: Task) {}
    fn abandon(&mut self, _ctx: &Context, _task: Task) {}
}
