"""The handler crate of the generic event loop (docs/spec.md §3.19), Rust only.

With `rust_loop = "<crate>"` in the [project] table (and a policy, so that the stepper exists), the
generator writes a second crate beside the stepper's, `<generated>/rust/<crate>/`:

- `src/event_loop.rs` is the generic event loop. It is one constant text, the same for every
  project: it steps a machine, calls a handler for each action the step returns, and takes the next
  event from a queue that the handlers (and the program) post to through a `Ctx`.
- `src/lib.rs` is what the model decides: the trait `Handlers` with one method per output action,
  typed from the model's sorts; the `Context` with one method per input event; and `App`, which
  ties the stepper to the loop.

The stepper crate is not touched: its files are what they were without this option.
"""

from __future__ import annotations

import re

from .header import block as _header_block
from .rustback import cargo_toml, ident

EVENT_LOOP = r'''//! The generic event loop. This text is the same for every project: nothing in it depends on the
//! model. It steps a machine with one event at a time, in the order the events were posted, and
//! calls the handler for every action a step returns, in the order the step returned them.
//!
//! The handlers run on the loop's thread, one at a time, and the next event is not taken before
//! they return: a handler that has to wait does its waiting elsewhere (a thread, a timer, an
//! operating-system call) and posts the outcome as an event through the context.

#![forbid(unsafe_code)]
#![deny(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::indexing_slicing)]

use std::sync::mpsc::{channel, Receiver, Sender};

/// What the loop steps: a model's stepper.
pub trait Machine {
    type Event;
    type Action;
    type Error;
    /// One step: the event in, the actions the model decides out, in order.
    fn step(&mut self, event: &Self::Event, out: &mut Vec<Self::Action>) -> Result<(), Self::Error>;
}

/// What the loop calls for each action.
pub trait Handler<C> {
    type Action;
    fn handle_action(&mut self, ctx: &C, action: Self::Action);
}

enum Msg<E> {
    Event(E),
    Stop,
}

/// The way to post events to a running loop. It can be cloned and sent to other threads.
pub struct Ctx<E> {
    tx: Sender<Msg<E>>,
}

impl<E> Clone for Ctx<E> {
    fn clone(&self) -> Self {
        Ctx { tx: self.tx.clone() }
    }
}

impl<E> Ctx<E> {
    /// Posts an event. Once the loop has ended the event is dropped.
    pub fn post_event(&self, event: E) {
        let _ = self.tx.send(Msg::Event(event));
    }

    /// Ends the loop once the events posted before this call have been stepped.
    pub fn stop_loop(&self) {
        let _ = self.tx.send(Msg::Stop);
    }
}

/// A machine, its handlers and the queue between them.
pub struct Loop<M: Machine, H> {
    machine: M,
    handlers: H,
    first: Vec<M::Action>,
    ctx: Ctx<M::Event>,
    rx: Receiver<Msg<M::Event>>,
}

impl<M: Machine, H: Handler<Ctx<M::Event>, Action = M::Action>> Loop<M, H> {
    /// `first` are the actions the machine returned while it was made.
    pub fn new(machine: M, first: Vec<M::Action>, handlers: H) -> Self {
        let (tx, rx) = channel();
        Loop { machine, handlers, first, ctx: Ctx { tx }, rx }
    }

    pub fn context(&self) -> Ctx<M::Event> {
        self.ctx.clone()
    }

    /// Runs until `stop_loop` is called, and gives the handlers back. An event the machine
    /// refuses ends the loop with the machine's error.
    pub fn run(self) -> Result<H, M::Error> {
        let Loop { mut machine, mut handlers, first, ctx, rx } = self;
        for a in first {
            handlers.handle_action(&ctx, a);
        }
        let mut out = Vec::new();
        while let Ok(Msg::Event(e)) = rx.recv() {
            out.clear();
            machine.step(&e, &mut out)?;
            for a in out.drain(..) {
                handlers.handle_action(&ctx, a);
            }
        }
        Ok(handlers)
    }
}
'''

_STD = {"Vec", "BTreeSet", "BTreeMap", "VecDeque", "Option", "String", "Box"}


def _ty(t: str) -> str:
    """A type of the stepper crate, written from the handler crate: names the stepper declares get `model::`."""
    return re.sub(r"\b[A-Z][A-Za-z0-9_]*\b", lambda m: m.group(0) if m.group(0) in _STD else "model::" + m.group(0), t)


def _arg(name: str, taken: set) -> str:
    n = ident(name)
    while n in taken or n in ("ctx", "self"):
        n += "_"
    return n


def _snake(label: str) -> str:
    return ident(label[2:])


def emit(model, em, package: str, rust_crate: str, workspace: bool, name: str) -> dict:
    """The files of <generated>/rust/<name>: {relative path: text}. `em` is the Rust emitter of the model (its
    `rtype` gives an argument's Rust type); `package` the stepper's Cargo package, `rust_crate` its directory."""
    from .rustback import camel

    inputs = sorted((a for a in model.actions if a.kind == "input"), key=lambda a: a.label)
    outputs = sorted((a for a in model.actions if a.kind == "output"), key=lambda a: a.label)
    crate = package.replace("-", "_")
    out = [_header_block("//").rstrip("\n"),
           "//! The handlers and the context of the model, over the generic event loop (docs/spec.md §3.19).",
           "//! Write `impl Handlers` for what each output action does, make an `App`, post the first events",
           "//! through its `Context`, and `run` it. The loop is `event_loop.rs`; it is the same in every project.",
           "",
           "#![forbid(unsafe_code)]",
           "#![allow(missing_docs, unused_variables, clippy::too_many_arguments)]",
           "",
           "pub mod event_loop;",
           "",
           "/// The stepper crate generated from the same model: its sorts, `Config`, `Event` and `Action`.",
           "pub use %s as model;" % crate, "",
           "/// The way to post the model's input events, to be cloned and sent to the threads that need it.",
           "pub type Context = event_loop::Ctx<model::Event>;", ""]
    # the handlers: one method per output action
    out += ["/// What the project does for each output action of the model, one method per action.",
            "pub trait Handlers {"]
    for a in outputs:
        names: set = set()
        params = []
        for n, s in zip(a.args, a.sorts):
            p = _arg(n, names)
            names.add(p)
            params.append("%s: %s" % (p, _ty(em.rtype(s))))
        out.append("    /// The model's output `%s(%s)`." % (a.label, ", ".join(a.args)))
        out.append("    fn %s(&mut self, ctx: &Context%s);" % (_snake(a.label), "".join(", " + p for p in params)))
    out += ["}", ""]
    # the dispatch
    out += ["impl<H: Handlers> event_loop::Handler<Context> for H {",
            "    type Action = model::Action;", "",
            "    fn handle_action(&mut self, ctx: &Context, action: model::Action) {", "        match action {"]
    for a in outputs:
        vs = ["a%d" % k for k in range(len(a.sorts))]
        out.append("            model::Action::%s%s => self.%s(ctx%s)," % (
            camel(a.label), "(%s)" % ", ".join(vs) if vs else "", _snake(a.label), "".join(", " + v for v in vs)))
    out += ["        }", "    }", "}", ""]
    # the context: one method per input event
    out += ["impl Context {"]
    for a in inputs:
        names = set()
        params = []
        for n, s in zip(a.args, a.sorts):
            p = _arg(n, names)
            names.add(p)
            params.append((p, _ty(em.rtype(s))))
        out.append("    /// Posts the model's input `%s(%s)`." % (a.label, ", ".join(a.args)))
        out.append("    pub fn %s(&self%s) {" % (_snake(a.label), "".join(", %s: %s" % pt for pt in params)))
        out.append("        self.post_event(model::Event::%s%s);" % (
            camel(a.label), "(%s)" % ", ".join(p for p, _ in params) if params else ""))
        out.append("    }")
        out.append("")
    out[-1:] = []
    out += ["}", ""]
    # the stepper as the loop's machine, and the app
    out += ["impl event_loop::Machine for model::State {",
            "    type Event = model::Event;", "    type Action = model::Action;", "    type Error = model::StepError;", "",
            "    fn step(&mut self, event: &model::Event, out: &mut Vec<model::Action>) -> Result<(), model::StepError> {",
            "        model::State::step(self, event, out)", "    }", "}", "",
            "/// The model's stepper, the generic loop and the project's handlers, tied together.",
            "pub struct App<H: Handlers> {", "    inner: event_loop::Loop<model::State, H>,", "}", "",
            "impl<H: Handlers> App<H> {",
            "    /// The stepper in its initial state for `config`, over `handlers`. Nothing runs before `run`.",
            "    pub fn new(config: model::Config, handlers: H) -> Result<Self, model::StepError> {",
            "        let mut first = Vec::new();",
            "        let state = model::State::new(config, &mut first)?;",
            "        Ok(App { inner: event_loop::Loop::new(state, first, handlers) })", "    }", "",
            "    /// A context to post events with. Events posted before `run` wait in the queue.",
            "    pub fn context(&self) -> Context {", "        self.inner.context()", "    }", "",
            "    /// Runs the loop until a handler or the program calls `stop_loop`, and gives the handlers back.",
            "    /// An event the model does not accept ends it with the stepper's error.",
            "    pub fn run(self) -> Result<H, model::StepError> {", "        self.inner.run()", "    }", "}", ""]
    dep = '\n[dependencies]\n%s = { path = "../%s" }\n' % (package, rust_crate)
    cargo = _header_block("#") + cargo_toml(name, workspace).replace('\n[features]\n# The firing hook (docs/spec.md §3.11.6): the output loop posts each fire\'s summand.\nfiring = []\n', "") + dep
    return {"Cargo.toml": cargo, "src/lib.rs": "\n".join(out) + "\n", "src/event_loop.rs": _header_block("//") + EVENT_LOOP}
