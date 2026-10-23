# naima-codegen specification

How naima-codegen produces code from a verified mCRL2 model. It covers what the
generator accepts, how it turns the model's open choices into one deterministic
policy without leaving what the model allows, which data structures the abstract
sorts become, what the generated Rust, Python and TypeScript code looks like from outside,
what it may cost per event, and how generation is repeated and checked at every
commit. Cite as `naima-codegen spec §3.7.2`.

## About this version

This is the generalised specification of naima-codegen, taken from version 5 of
the code generator specification of the first project that used the generator
and freed of what was particular to that project. Section numbers are those of
version 5, and versions 4 and 5 number the same content alike, so a citation of
either by section number points at the same content here. A section whose content
belonged to that project keeps its number with a note that it is left to the
project. Two sections are new: §3.6.9, the project table, and §3.18, the TypeScript
backend, numbered after the last section of version 5 so that no number moves. Version
5 had two backends, with Python as the oracle the tests compared Rust with; here the
backends are peers (§3.12, §3.16).

Where the generator does not yet do what a section says, the section has a line
that starts with "Not implemented yet".

## 1. Scope

Covered here:

- the files the generator reads and writes, and the mCRL2 tools it runs (§3.1, §3.2);
- the subset of mCRL2 the generator accepts in the process it generates from (§3.3–§3.5);
- the interface declaration that classifies actions and parameters, and the project
  table that says where the files are (§3.6);
- the policy that resolves the model's nondeterminism, the refined process built from it,
  and the checks that the refinement stays inside the model (§3.7, §3.9);
- the meaning of the generated code: the step function, the initial state, the acceptor
  a runtime monitor is built on (§3.8);
- the catalogue of data structures, per backend (§3.10);
- the three backends, peers a project chooses among, and the interface each presents:
  Rust (§3.11), Python (§3.12) and TypeScript, with a Svelte 5 module for user
  interfaces (§3.18);
- the cost and memory rules the generated code must meet (§3.13);
- the errors of the generator and of the generated code (§3.14);
- regeneration and the diff check at every commit (§3.15);
- how each backend is checked against mCRL2 and against the others (§3.16, §3.18.5);
- the reference instances the refinement checks run on (§3.17).

Left to the project that uses the generator: the model itself and its properties
(what the component decides, the actions and events, their guards and effects),
written in the subset fixed here; the runtime that embeds the generated code
(threads, the event queue, writing the action record, where the monitor runs); and
the budgets and requirements the project holds the generated code to.

This specification does not argue that the model's abstraction of sizes and counts
is sound at the sizes of real runs. The generated code evaluates the model's
conditions on real numbers, and what the model checker proved on small instances
carries over only as far as the project's own abstraction argument says. What this
specification guarantees is narrower and mechanical. The generated code does, on
every event, exactly what the refined model does, and the refined model does nothing
the model forbids.

## 2. Terms

- **Project**: the directory the generator works on, with its project table (§3.6.9).
- **Model**: the mCRL2 specification of the component and its surroundings, in the
  project's model directory (§3.6.9).
- **Core**: the one process of the model that describes the component code is
  generated for. Code is generated from Core and from nothing else.
- **Environment**: the processes of the model that describe everything Core talks
  to. Never generated.
- **System**: Core in parallel with the environment, joined by `comm` and `allow`,
  on reference instances. Properties are verified on systems.
- **LPS**: the linear process specification that `mcrl22lps` produces: a list of
  process parameters, an initial state and a list of summands.
- **Summand**: one alternative of the LPS,
  `sum x1: S1, ..., xn: Sn . c -> a(e1, ..., em) . P(p1 = g1, ...)`, with sum variables
  `x1 … xn`, condition `c`, one action `a` with argument expressions `e1 … em`, and
  next-state assignments `g` (a parameter not assigned keeps its value). The
  `true -> delta` summand that `mcrl22lps` adds is not counted as a summand.
- **State**: a value for every process parameter of Core.
- **Label**: an action name. Core's labels start with `c_`; the **vocabulary name** of
  a label is the label without `c_` (§3.5.6).
- **Input** and **output**: the two kinds of label (§3.6). An input is something the
  environment tells the component (a job finished, a resource was released, a
  reading, a stop request); an output is something the component decides (start a
  job, release a resource).
- **Caller**: the code that embeds the generated code, hands it events and carries
  out the actions it returns.
- **Event**: one occurrence of an input label with argument values, handed to the
  generated code by the caller.
- **Action**: one occurrence of an output label with argument values, returned by the
  generated code to the caller.
- **Entry**: one line of the action record: an event or an action, with its sequence
  number.
- **Enabled**: a summand is enabled in a state, for values of its sum variables, when
  its condition evaluates to true.
- **Quiescent**: a state in which no output summand is enabled for any values of its
  sum variables.
- **Policy**: the file that resolves the choices the model leaves open (§3.7).
- **Selector**: the term the policy gives for one free sum variable of an output
  summand: the value the refined Core takes for it (§3.7.1).
- **Index**: a candidate structure declared in the policy: the ids of one id sort
  that satisfy a membership predicate, in a declared order, maintained by the
  generated code at every write it depends on (§3.7.6).
- **Head-enabled**: a summand is head-enabled when, in every reachable state in
  which the model enables it for some value of its free sum variables, the
  refined Core enables some output. Choosing the head of its candidate structure
  then never leaves the component idle where the model could act (§3.9.3).
- **Deferred**: an output summand the policy exempts from head-enabledness, with
  its reason; its progress is carried by the properties re-verified on the
  refined system (§3.7.1, §3.9.3, §3.9.4).
- **Refined Core**: Core with the policy applied; deterministic.
- **Stepper**: the generated code of the refined Core: the transition function.
- **Acceptor**: the generated code of the unrefined Core, which checks a recorded
  entry against the model; a runtime monitor is built on it.
- **Backend**: a target language, Rust, Python or TypeScript. The backends are peers:
  a project chooses any of them (§3.6.9), each is checked against mCRL2 itself, and
  the tests compare them with each other (§3.16). None is the reference of another.
- **Catalogue**: the abstract data types the generator knows, each with an mCRL2
  reference definition, an implementation per backend, a cost per operation and a
  conformance test (§3.10).
- **Id sort**: a structured sort with one constructor and one field of sort `Nat`,
  such as `sort Job = struct job(ix: Nat);`, declared as an id sort in the interface
  declaration. Its values number the things the component keeps state for, one
  entry per id.
- **Column**: a Core parameter of sort `I -> X` with `I` an id sort, read by
  application and changed by function update; stored as an array indexed by the id.
- **Per-id collection**: a collection that is the value of a column at one id, such
  as the inputs of one job.
- **Frontier structure**: a Core parameter that is a collection and not a column,
  declared `frontier` in the interface declaration (§3.6). Its size is bounded by the
  number of ids still active, not by the number of ids ever created.
- **Cost class** of an operation: `O(1)`, `O(log F)` with `F` the size of the frontier
  structure it works on, or `O(k)` with `k` the size of the per-id collection or
  action argument it works on (§3.13.1).
- **Reference instance**: one fixed, small environment configuration on which the
  model and the refinement checks are run (§3.17).
- **Generated files**: everything under the project's generated directory (§3.1.2).

## 3. The specification

### 3.1 What the generator reads and writes

The generator is one command, `naima-codegen generate`, run on a project
(`--project DIR`, by default the current directory; also `python -m naima_codegen`).
Paths below are relative to the project directory. `<model>`, `<generated>` and
`<catalogue>` stand for the model, generated and catalogue directories that the
project table names (§3.6.9).

#### 3.1.1 Inputs

| file | content | read for |
|---|---|---|
| `<model>/core.mcrl2` | the data definitions Core uses (sorts, maps, equations, catalogue definitions copied verbatim), the `act` declarations of Core's labels, and `proc Core(...)` | generation and checks |
| `<model>/codegen-init.mcrl2` | one line `init Core(v1, ..., vk);` giving a well-typed initial state; the values of configuration parameters in it are placeholders | generation |
| `codegen.toml` | the project table (§3.6.9) and the interface declaration (§3.6) | generation |
| `<model>/policy.toml` | the policy (§3.7); optional | generation |
| `<catalogue>/<entry>/` | per catalogue entry: `reference.mcrl2`, `ops.toml`, `rust.rs`, `python.py` (§3.10) | generation |
| `<model>/env.mcrl2`, `<model>/instances/<name>.mcrl2` | the environment and the reference instances (§3.17) | checks of §3.9 only |
| `<model>/observers.mcrl2` | optional: observer processes, whose labels start with `o_`, composed into the refined systems (§3.9.2, §3.9.4) | checks of §3.9 only |
| the property items (§3.6.9) | the property set, each an `.mcf` file | checks of §3.9 only |

The generation input is the concatenation, in this order, of `<model>/core.mcrl2`
and `<model>/codegen-init.mcrl2`. A system is the concatenation of
`<model>/core.mcrl2` (or a generated replacement of it, §3.7.4), `<model>/env.mcrl2`
and the instance files of its group (§3.17), which hold the `init` with `allow`,
`comm` and the instances' values.

When `<model>/policy.toml` is absent, the generator writes only what needs no
policy (the configuration, the initial state, the user maps and the acceptor), no
stepper and no systems, and the manifest says so (`"stepper": null`).

#### 3.1.2 Outputs

| file | content |
|---|---|
| `<generated>/model/core-refined.mcrl2` | the refined Core, a drop-in replacement of `<model>/core.mcrl2` (§3.7.4) |
| `<generated>/model/core-probe.mcrl2` | the probe Core used by the non-blocking check (§3.9.3) |
| `<generated>/model/probe.mcf` | the properties of the probe checks (§3.9.3, §3.9.6, §3.9.7) |
| `<generated>/model/systems/<group>-{model,refined,probe}.mcrl2` | the assembled systems the checks of §3.9 run on (§3.9.7) |
| `<generated>/rust/<rust_crate>/Cargo.toml`, `src/lib.rs` | the Rust stepper, acceptor and types (§3.11) |
| `<generated>/rust/<rust_crate>/src/py.rs` | the Python binding of the Rust backend, for the comparison of the backends only (§3.11.4) |
| `<generated>/python/<python_package>/__init__.py` | the Python stepper, acceptor and types (§3.12) |
| `<generated>/typescript/<typescript_module>/<typescript_module>.ts` | the TypeScript stepper, its transition table, acceptor and types, without Svelte (§3.18.3) |
| `<generated>/typescript/<typescript_module>/<typescript_module>.svelte.ts` | with a policy: the stepper as a Svelte 5 class (§3.18.4) |
| `<generated>/MANIFEST.json` | the record of one generation (§3.15.2) |

Only the files of the backends the project table names are written (§3.6.9).

Not implemented yet: `src/py.rs` (§3.11.4).

3.1.3. Nothing under `<generated>` is edited by hand. Every generated file starts
with a comment block, in the file's comment syntax, that says: the file is
generated, by naima-codegen, from the model's `core.mcrl2`, `codegen.toml` and
`policy.toml`, naming `MANIFEST.json` in the generated directory; that any edit is
lost at the next generation and fails `naima-codegen generate --check` (§3.15);
and what to do instead: change the model, check it, run `naima-codegen generate`,
and commit the model and the generated directory together. §3.15 detects any edit;
a project's build may run `generate --check` first and refuse to build while the
generated directory differs from what the model generates.

3.1.4. The generator reads no other file, no environment variable and no clock,
so that the same inputs give the same outputs on every machine (§3.15.1). The
mCRL2 tools are found on `PATH`.

### 3.2 Linearisation

3.2.1. The mCRL2 toolset is pinned. The pin is `mcrl2_version` in `codegen.toml`
(`202607.0` when this specification was written); the generator runs
`mcrl22lps --version` and refuses to run (error G1, §3.14.1) when the version
differs. Moving the pin is a commit that changes `codegen.toml` and regenerates.

3.2.2. The LPS is produced by exactly

```sh
mcrl22lps --lin-method=regular --no-constelm -n <input>.mcrl2 <input>.lps
lpspp --format=internal <input>.lps <input>.aterm
```

`-n` (`--no-cluster`) keeps one LPS summand per source summand. Without it,
`mcrl22lps` clusters the summands of one label into one summand over a fresh
sum variable of an enumeration (checked on 2026-10-07 on a model of 80
summands: they became 62 and `delta`, 18 of them over `Enum3`, `Enum5` or a
fresh `Bool`), renames their sum variables after the cluster and may add a
fixed dummy variable; §3.5.1, §3.5.3 and §3.5.4 assume one summand per
source summand.

The generator reads only the `.aterm` file, a typed ATerm in which every
variable and every operator carries its sort, so no sort is guessed. The
pretty-printed form is never parsed.

3.2.3. `--no-constelm` and `-n` are required. Without `--no-constelm`, `mcrl22lps`
replaces a parameter that no summand changes by its initial value. Checked on
2026-10-06 with a Core whose worker count `W` was a parameter initialised to 2: the
default run removed `W` and printed the condition `nrun < 2`, which would freeze a
configuration value into the generated code. No other option is passed. Data
rewriting stays on, so the generator reads the rewriter's normal forms, which are
deterministic for a fixed toolset version.

3.2.4. The same two commands produce the refined LPS from
`<generated>/model/core-refined.mcrl2` followed by `<model>/codegen-init.mcrl2`.

3.2.5. When `mcrl22lps` introduces global variables (`glob`, values that no longer
matter), the generated code gives such a parameter the default value of its sort
(§3.10.2).

### 3.3 The accepted subset: sorts

3.3.1. `Bool`, `Pos`, `Nat` and `Int` are accepted. `Real` is refused: the generated
code does no fractional arithmetic, and a rational would have no fixed-width
representation. A quantity a project would write as a fraction or in seconds is a
scaled integer here (parts per thousand, microseconds). A constant fraction `a/b` of
a quantity `x` is `(a · x) div b`, and a ratio of two quantities is scaled before it
is divided; `div` rounds down, and `x ∸ y` is `max(0, x − y)`, so the model and both
backends round alike.

3.3.2. Structured sorts are accepted in three shapes: an *enumeration* (every
constructor without fields), a *record* (one constructor with fields), a *union*
(several constructors, some with fields). A structured sort that contains itself,
directly or through other sorts, is refused.

3.3.3. An *id sort* is a record with exactly one field of sort `Nat`, declared in
`codegen.toml` (§3.6.4). Only id sorts index columns.

3.3.4. `List(X)`, `FSet(X)` and `FBag(X)` are accepted when `X` is an accepted sort
other than a function sort or a collection. `Set(X)` and `Bag(X)`, which may be
infinite, are refused.

3.3.5. A function sort `A -> B` is accepted only as the sort of a Core parameter, of
the constant lambda that initialises it (§3.4.4), and of an argument or result of
a user map under the rules of §3.4.8. With `A` an id sort the parameter is a
column, with `A` an enumeration it is a fixed table, otherwise a hash map
(§3.10). A function sort is refused as an action argument and inside a structured
sort or a collection.

3.3.6. A catalogue sort is a sort whose definition is copied verbatim from
`<catalogue>/<entry>/reference.mcrl2` (§3.10.1). The generator recognises it by
name and checks that the copied text is byte-identical to the catalogue file
(error G7 otherwise), so the model checker verified exactly the semantics the
backend implementations are tested against.

3.3.7. A sort alias (`sort A = B;`) is accepted but gives no distinction, because
aliases disappear in the LPS (checked: `sort Id = Nat;` made every parameter of sort
`Id` appear as `Nat`). Every distinction the code needs, such as an id against a
count, is made with a structured sort.

3.3.8. Names share one namespace in mCRL2 (checked: a parameter `ready` clashes with
a constructor `ready` and linearisation fails). A name that is a reserved word of
Rust, Python or TypeScript is emitted in that backend with a trailing underscore. The rule is
fixed, so every backend and every regeneration agree.

### 3.4 The accepted subset: maps and equations

3.4.1. Built-in operators of the accepted sorts are accepted: arithmetic (`+ - * div
mod min max succ pred abs`), comparisons, Boolean connectives, `if(c, a, b)`, the
conversions `Int2Nat`, `Nat2Pos`, `Pos2Nat`, `Nat2Int`; on lists `[]`, `|>`, `<|`,
`head`, `tail`, `rhead`, `rtail`, `#`, `.`, `++`, `in`; on finite sets `{}`, `{x}`,
`+`, `-`, `*`, `in`, `#`, `<=`; on finite bags `{}`, `count`, `+`, `-`; function
application and update `f[x -> v]`; constructors, projections and recognisers of
structured sorts. Whether an operator is allowed in a given place depends on its
cost class there (§3.13.1).

3.4.2. A user map (declared in `<model>/core.mcrl2`, not from the catalogue) must
satisfy all of the following.

1. Its definition is not recursive, except a walk's call of itself (§3.4.8): the
   graph "map f's equations mention map g" over user maps has no cycle other than
   a walk's self-loop.
2. Its equations are unconditional.
3. It has one equation whose left side applies it to distinct variables; or its
   first argument ranges over an enumeration or union and it has exactly one
   equation per constructor, the other arguments being distinct variables, with no
   nested pattern; or it is a walk (§3.4.8).
4. An argument or result of a function sort follows the rules of §3.4.8.

A conditional choice is written with `if` in the right side. The generator checks
1–4 (error G3). The generated code contains no recursion, so that no operation fails
because a value is deep, and an algorithm over a collection belongs in the
catalogue, where its cost is stated and its implementations are tested. A pattern
equation on set constructors, such as `f({i} + s) = ...`, does not even rewrite
(checked: `lps2lts` stopped with "does not rewrite to true or false" on it).

3.4.3. A map without arguments (a constant) is accepted only when it is listed under
`[constants]` in `codegen.toml`; it is then emitted as a constant of each backend.
Any other constant is refused (error G3), because it would freeze an instance value
into the code; configuration values are Core parameters (§3.6.2).

3.4.4. A lambda is accepted only as the initial value of a function-sorted
parameter and only when its body is closed and does not mention the bound variable
(`lambda n: Job . idle`). It gives the default value of the column or table.

3.4.5. `exists` and `forall` are accepted only in conditions and only bounded by a
per-id collection, as in `exists x: X . x in col(n) && p(x)` and
`forall x: X . x in col(n) => p(x)`, with `col` a column. They compile to a loop over
the collection, of cost class `O(k)`. Every other quantifier is refused (error G3),
including one bounded by a frontier set (`forall m: Job . m in W => …`, whose cost
grows with the frontier). Such a condition is written over a count or an emptiness
test (`W == {}`) instead.

3.4.6. `whr` clauses are accepted and compile to local bindings.

3.4.7. The equations of a catalogue entry are not compiled: the entry's
implementation in each backend is used instead (§3.10).

3.4.8. Walks. A *walk* is a user map `f` with one argument position `i` of a
`List(X)` sort and exactly two equations, `f(…, [], …) = b` and
`f(…, x |> l, …) = R`, whose other arguments are the same distinct variables in
both, such that:

1. `b` does not mention `f`;
2. `R` is a *walk body*, one of: `f(a1, …, ak)` with `l` at position `i` and every
   `aj` free of `f` (continue, with new accumulators); an expression free of
   `f` (exit with that value); `if(c, R1, R2)` with `c` free of `f` and `R1`,
   `R2` walk bodies; `e ⊕ R1` with `e` free of `f`, `R1` a walk body and `⊕`
   one of `||`, `&&`, `+`, `|>`; or `R1 ⊕ e` with `⊕` one of `||`, `&&`, `+`.
   One walk uses one operator `⊕` at most;
3. `whr` clauses are allowed in `R` and bind expressions free of `f`.

The generator compiles a walk into one loop over the list, with its arguments
as loop variables, never into a recursive function. A continue updates them, an
exit or the end of the list ends the loop, and `⊕` accumulates (`||` and `&&`
short-circuit; `|>` collects a prefix, which the exit or end value follows).
Its cost class is the list's length times the cost of `R` without the call:
`O(k)` when that is `O(1)`, and `O(k²)` when `R` itself costs `O(k)` on a per-id
collection or a list argument (such as `n in l`). `O(k²)` is accepted only in a
walk (§3.13.1). Walks are not catalogue entries, and nobody writes them by hand in
any backend.

Function-sorted arguments and results of a walk or of a non-recursive user map
are accepted when, at every use in Core:

- a function-sorted argument is a Core parameter (a column, table or map) or a
  function-sorted argument of the calling map, passed whole, and the code reads it
  in place;
- a function-sorted result is assigned to the very parameter passed in the same
  argument position, `p = f(…, p, …)`. The code updates `p` in place, after
  every other right side of the summand that reads `p` has been evaluated
  (§3.8.4); two such in-place updates that read each other's parameters are G8;
- a function-sorted accumulator that the map updates but does not return is an
  **overlay**: a hash map of the entries written during the call, over the
  unchanged parameter, `O(1)` per read and write, dropped at the end of the call.

Anything else of a function sort is G3.

### 3.5 The accepted subset: the Core process

3.5.1. `<model>/core.mcrl2` declares exactly one process, `Core`, written as a linear
process:

```
proc Core(p1: S1, ..., pk: Sk) =
       sum x1: T1, ... . c1 -> c_a1(e11, ...) . Core(p = g, ...)
     + ...
     + delta;
```

Each summand has a condition, exactly one action, and recursion into `Core` with
named assignments; the `sum` is optional, and so is `+ delta`. Refused in Core
(error G4): sequential composition other than `action . Core(...)`, calls to other
processes, parallel composition, `allow`, `comm`, `hide`, `rename`, `block`, time
(`@`), `tau`, multi-actions, the `c -> p <> q` form, and summands without an action.
One source summand is then one LPS summand and one block of generated code, the
parameters keep their names and order (linearisation adds no program counter),
and a regenerated diff stays readable. The environment is free of this rule.

3.5.2. The generator checks that the LPS parameters are, in order and with their
sorts, exactly the parameters listed in `codegen.toml` (§3.6.2). A parameter added
by linearisation (a program counter of sort `Pos`) is therefore an error (G4).

3.5.3. Names are recovered from the LPS. `mcrl22lps` appends `_Core` and digits to
parameters and sum variables (checked: `st` became `st_Core5` and a sum variable `i`
became `i_Core1`), and the generator removes a suffix matching `_Core[0-9]*$`. Within
a summand, sum variables whose recovered names coincide are numbered in order of
appearance.

3.5.4. A summand is identified by its label and, when several summands share a
label, by its ordinal among the summands of that label in the source order of
`proc Core` in `<model>/core.mcrl2`, inputs and outputs counted together: the first
is `c_fire`, the second `c_fire#2`, and so on. That is how the policy and the
generated code name a summand, and a person writing the policy counts it in the
file. The generator reads the source order from `<model>/core.mcrl2` itself (the
label after each `->` and the identifiers declared after each `sum`, as for
§3.5.8) and checks that the LPS lists the same summands in the same order, with
the same labels and the same recovered sum variable names (§3.5.3); any
difference is G4. With the pinned command of §3.2.2, `mcrl22lps` lists summands
in source order (checked on 2026-10-07 on a model of 82 summands), and the check
catches a toolset that reorders.

When the model has a policy (§3.7), every summand of `proc Core` follows a tag line
of its own, at the start of the line: `%@input` for an input summand, or
`%@order <level>` for an output summand, the level being a number the model's
author uses to group outputs (the policy's `order` decides; the level is kept in
the refined Core's text). The refinement reads the summands from these tags; a tag
that says input for a summand `[[action]]` declares as output, or the reverse, is
G10. Before the final `+ delta` the body has the line `%@observers`, where the
systems of §3.9.7 insert the observers' summands (§3.17).

3.5.5. Every sum variable of a summand occurs as a whole argument of its action, or
as a field, at any depth, of a constructor application that is an argument of its
action (`c_start(task(n, K1))`, where `n` is bound by projecting the first field
of `task`), or is fixed by a top-level conjunct `x == e` (or `e == x`) of its
condition where `e` does not mention `x` (error G5 otherwise). For an input summand
the event's argument is matched against the constructor (its recogniser, for a
union) and each field that is not a sum variable is compared for equality; a
mismatch means the summand does not accept the event. For an output summand the
argument is built from the bound variables. An event then binds every variable
of an input summand without search, and a record entry determines the next state
of the acceptor, so the monitor never tracks a set of states.

3.5.6. Every label of Core starts with `c_`, and the environment uses `e_` for its
side; an instance composes them as `comm({c_fire | e_fire -> fire, ...})` and
`allow({fire, ...})`. With `comm({a | e_a -> a})` and `allow({a})`, Core could
still perform `a` alone, without the environment (checked on 2026-10-06: the
uncommunicated `a` transitions appear in the state space). The vocabulary name,
`fire`, is what properties, the action record and the generated code use.

3.5.7. Action arguments are of accepted scalar sorts, id sorts, enumerations, and
records and unions (§3.3.2) whose fields are of these sorts or lists of them, nested
to any finite depth (a record with a list field, a union of such records), or lists
of any of these. A list argument, or a list inside one, is copied in `O(k)`. A
function sort, a set or a bag is refused as an action argument (error G12).

3.5.8. No sum variable of Core has the name of a Core parameter (error G4).
`mcrl22lps` resolves the name to the sum variable inside the summand. When the sorts
differ, linearisation fails (G2; found on 2026-10-07 with a sum variable
`ws: List(W)` against a parameter `ws: Nat`), and when they agree it succeeds
silently and the sum variable becomes the parameter's implicit assignment. The
generator checks the names on `<model>/core.mcrl2` itself, before linearising, by
comparing the identifiers declared after each `sum` in `proc Core` with the
parameters.

### 3.6 The interface declaration: `codegen.toml`

`codegen.toml` holds the project table (§3.6.9) and the interface declaration of
Core, described in §3.6.1 to §3.6.8.

3.6.1. `mcrl2_version`: the pinned toolset version (§3.2.1).

3.6.2. `[[parameter]]`, one per Core parameter in LPS order, each with:

- `name`: the recovered name;
- `sort`: optional; the parameter's sort as mCRL2 text, checked against the LPS
  when present (G6);
- `role`: `config` (no summand assigns it, which is checked, error G6; it becomes a
  field of the configuration passed to the constructor), `column` (a function over
  an id sort), `table` (a function over an enumeration), `map` (a function over any
  other sort, a hash map such as `K -> V`), `frontier` (a collection whose size is
  bounded by the active ids), or `scalar` (anything else). A function-sorted
  parameter whose role does not match its domain is G6;
- `init`: optional; an mCRL2 expression over `config` parameters giving the
  parameter's initial value (`credits = "2 * w"`, `opened = "mode == NONE"`).
  The constructor computes it from the configuration it is given. The value in
  `<model>/codegen-init.mcrl2` is then a placeholder, and the generator checks that
  the expression evaluated on the placeholder configuration equals it (G6). So that
  mCRL2 types each expression, the generator adds to the generation input, before
  linearising, one map `gen_init_<parameter>` per `init`; the names `gen_init_*` are
  reserved for it. Without `init`, the initial value is the one in
  `<model>/codegen-init.mcrl2`, which must then not depend on the configuration;
- `repr`: the catalogue representation (§3.10.3), when the sort admits more than one;
- `width`: for a `Nat`, `Int` or `Pos` value, or a column of them, one of `u8 u16 u32
  u64 i32 i64`; default from `[widths]`.

3.6.3. `[[action]]`, one per label and signature, with `label`, `kind` (`input` or
`output`) and `args`, the field names of the action's arguments in the generated
types. Every label of Core is listed and nothing else (error G6). A label may be
declared by Core with two signatures only when one is an input and the other an
output and their arities differ (`c_link` as an input with three arguments and as an
output with two). It then has two `[[action]]` entries, and the front end tells them
apart by their number of arguments. Any other overloading is G6.

3.6.4. `id_sorts`: the names of the id sorts.

3.6.5. `[constants]`: the constant maps that may appear (§3.4.3), as a table keyed
by the constants' names, each value the constant's sort as mCRL2 text.

3.6.6. `[widths]`: default widths by sort. When absent, `Nat` and `Pos` are `u64`,
`Int` is `i64`, and an id sort is `u32` (4,294,967,295 ids).

3.6.7. `[[observable]]`: a `name`, an optional id argument (its name `arg` and its
`sort`, an id sort), and an mCRL2 expression `expr` over the parameters (and the
argument). The generator emits a read-only getter for each (§3.11.2), and refuses
one whose cost class is not `O(1)` or `O(log F)` (error G8). Observables are how the
caller reads, between steps, what it reports (such as the count of ids in each state,
or the state of one id) without reading the state's layout.

Not implemented yet: the getters, in any backend, and the cost check of an
observable. The front end reads and checks the declaration only.

3.6.8. For each input label `c_x` with argument sorts `T1 … Tm`, the generator
declares `c_x_refused: T1 # ... # Tm` and `refused_x: T1 # ... # Tm`, and it declares
`probe_enabled`, `probe_quiet` and `probe_ambiguous` without arguments, in the probe
Core (`<generated>/model/core-probe.mcrl2`) only. For an input label with two
summands it also declares `c_x_ambiguous` and `ambiguous_x`, so that the environment
supplies the arguments of the check of §3.9.6. When it assembles a probe system
(§3.9.7) it adds `c_x_refused | e_x -> refused_x` (and `c_x_ambiguous | e_x ->
ambiguous_x`) to the instances' `comm` and every `refused_x`, `ambiguous_x` and
`probe_*` label to their `allow`. Neither `<model>/core.mcrl2` nor the instance files
declare or mention them; a declaration of one there is G6. They are a mechanical
function of Core's input labels, and the model's own systems never use them.

3.6.9. The project table. A project is a directory, given by `--project DIR` and by
default the current directory. The generator looks for `<project>/codegen.toml` and,
when it is not there, for `<project>/model/codegen.toml`; in the latter case the
model directory is `model`, and a table that names another is G6. Both files at
once is G6. Its `[project]` table has these
keys, every path relative to the project directory unless it says otherwise:

- `model`: the model directory, default `"model"`. It holds `core.mcrl2`,
  `codegen-init.mcrl2`, and optionally `policy.toml`, `env.mcrl2`, `observers.mcrl2`
  and `instances/*.mcrl2` (§3.1.1);
- `generated`: the generated directory, default `"generated"` (§3.1.2);
- `catalogue`: the catalogue directory, default `"catalogue"` (§3.10.1);
- `properties`: optional; a directory of property items. Each item is a directory
  `<item>/` with a `meta.json` that holds `"model": "<model>/systems/<group>.mcrl2"`
  (the system of the group the property is about), `"verifier": "mcrl2"`, and
  `"property"`, the path of its `.mcf` file relative to the project (§3.9.4). An
  item may name its group with `"group": "<group>"` instead of `"model"`. Items
  with another verifier are ignored;
- `backends`: the backends the project chooses, a non-empty list of `"rust"`,
  `"python"` and `"typescript"`, default `["rust", "python"]` (what a project got
  before the key existed). Only the chosen backends' files are generated, and only
  their names below are required;
- `rust_crate`: required with `rust`; the crate's directory under `<generated>/rust/`
  (§3.11.1);
- `rust_package`: the Cargo package name, default the value of `rust_crate`;
- `rust_workspace`: a Boolean, default `false`. With `true`, the generated
  `Cargo.toml` inherits `version`, `edition`, `rust-version`, `license`, `publish`
  and the lints from the enclosing Cargo workspace; with `false` it is a standalone
  manifest;
- `python_package`: required with `python`; the package is
  `<generated>/python/<python_package>/__init__.py` (§3.12.1);
- `typescript_module`: required with `typescript`, letters, digits, `_` and `-`; the
  modules are `<generated>/typescript/<typescript_module>/<typescript_module>.ts` and
  `.svelte.ts` (§3.18.1).

An unknown key, a value of the wrong type, or a path that is absolute or leaves the
project is G6. A missing required key is G6 when the outputs are built (`generate`
and `generate --check`); `generate --front-end` does not need the names.

The `[systems]` table says how the systems of §3.9.7 are assembled; §3.17 gives its
keys.

The command line is

```sh
naima-codegen generate [--check | --front-end] [--project DIR]
naima-codegen check-refinement [--properties | --no-model] [--project DIR] [group...]
```

and the same with `python -m naima_codegen`. `generate` writes the generated
directory, `--check` compares instead (§3.15.3), and `--front-end` only judges the
model (§3.1–§3.6, §3.14.1). `check-refinement` runs the checks of §3.9 on the given
groups, every group by default.

### 3.7 The policy and the refined Core

The model may leave choices open: which of several enabled outputs happens first,
and which value a sum variable of an output takes (which id to select, which item to
release). The code must take one, at the cost of §3.13. The policy says which, and
the refined Core is the model with that choice written into it, so that the choice
is itself model-checked (§3.9). A model may leave the choice among equals open on
purpose, keeping orderings out of its state so that the state space stays small; the
policy is where they come back.

#### 3.7.1 The policy file: `<model>/policy.toml`

A TOML file with these keys. Every term in it is mCRL2 text over Core's parameters,
the maps of `<model>/core.mcrl2` and of `maps`, constructors, and the three selection
operations of §3.7.6.

- `order`: the list of every output summand (§3.5.4), highest priority first. It is
  total: an output summand missing from it, a name listed twice, or a name that is
  not an output summand, is error G9.
- `[select."<summand>"]`: for every sum variable `x` of an output summand that is
  not fixed by an equality conjunct (§3.5.5), an entry
  `x = { term = "<term>", when = "<Boolean term>" }`. `term` is the value chosen;
  `when` is the condition under which `term` is defined (for example
  `W != {}` before `least(W)`). Both are written over the parameters and the
  summand's sum variables listed before `x` in the entry order of the table. A
  missing selector, or a selector for a variable the summand does not have or that
  an equality fixes, is G9.
- `[index.<name>]`: the indexes, §3.7.6.
- `maps`: optional; mCRL2 text declaring maps (with `var` and `eqn`) that selectors
  use. They are user maps under §3.4.2 (walks included, §3.4.8), compiled like those
  of `<model>/core.mcrl2` and appended to the refined Core only. Their names start
  with `p_` (G9 otherwise), so they never clash with the model's.
- `[defer]`: optional; `"<summand>" = "<reason>"` for each deferred output summand
  (§2). A summand may be deferred only when its condition has a conjunct that
  depends on the chosen value through a parameter read at another id (a column
  read at `next(n)`, a quantifier over the chosen id's collection), or when the
  policy deliberately serves only the head of a structure that the model serves
  from its head. The reason says which. A deferral of a summand that has no free sum
  variable is G9.

A selector term uses only operations whose result is fully determined: the three
operations of §3.7.6, the top of a priority queue, the head of a queue, the top of
a stack, `os_min` of an ordered set, a map of `maps`, and `if` over finitely many
constructors (such as the first constructor, in declaration order, that satisfies a
condition). mCRL2 has no operation that picks an unspecified element of a finite
set, and the generator adds none, because a choice that depended on hash order would
differ between the backends.

#### 3.7.2 Construction

Let the output summands be numbered `1 … m` in policy order. For output summand `j`
with sum variables `x̄`, condition `c_j`, selectors `t̄` with conditions `w̄`, and
equality-fixed variables `ȳ := ū`:

1. `σ_j` is the substitution `x̄ := t̄, ȳ := ū`, applied in the order of the
   selector table and then of the equalities; `c'_j = w_j && c_j[σ_j]`, where
   `w_j` is the conjunction of the selectors' `when` terms (`true` when there are
   none). The refined summand has no sum variable; its action arguments and
   next-state assignments are those of `j` under `σ_j`, plus the upkeep of the
   entry-order indexes (§3.7.4).
2. The refined condition of output summand `j` is
   `c'_j && !c'_1 && ... && !c'_(j-1)`: it fires only when no output before it in the
   policy is enabled.
3. Every input summand `i` keeps its sum variables and gets the condition
   `c_i && Q`, with `Q = !c'_1 && ... && !c'_m`, so an input is taken only in a
   quiescent state. This is run-to-completion: the component finishes all it has to
   decide before it looks at the next event. Its next-state assignments gain the
   upkeep of the entry-order indexes as in 1.
4. Equality-fixed variables of input summands are substituted as in 1.

`when` comes first in `c'_j` so that a selector is never evaluated where it is
undefined. In the generated code `&&` evaluates left to right and stops at the first
false operand, and in mCRL2 `false && b` rewrites to `false` whatever `b` is.

Choosing the head of a candidate structure is a refinement only where the head is
enabled whenever some candidate is; where it is not, the refined Core stops where the
model goes on. The non-blocking check (§3.9.3) finds every such state on the reference
instances. The policy then changes, or the model gets an exact worklist, or the
summand is deferred with its reason (§3.7.1).

#### 3.7.3 Summands that share a label

Summands that share a label are mutually exclusive in the unrefined model: no
reachable state enables two of them with the same action arguments. This is checked
(§3.9.6), not assumed. With it, an event or an entry selects at most one summand, and
the policy needs no order among inputs.

#### 3.7.4 The refined Core as mCRL2

`<generated>/model/core-refined.mcrl2` is `<model>/core.mcrl2` with the body of
`proc Core` replaced by the refined summands, and with three additions; the rest is
copied verbatim. It can therefore replace `<model>/core.mcrl2` in any system
(§3.1.1), with the initial values of the added parameters appended to the `init`.

1. The maps of the policy's `maps`.
2. For every selection operation the policy uses, its reference definition: a
   map the generator writes, with conditional equations, whose value is the one
   §3.7.6 defines. `least(W)` over `FSet(I)` is `ix_least_W(W, 0)` with

   ```
   job(i) in S -> ix_least_W(S, i) = job(i);
   !(job(i) in S) -> ix_least_W(S, i) = ix_least_W(S, i + 1);
   ```

   (for an id sort with constructor `job`). It is evaluated only under `W != {}`
   and therefore terminates. The equations are conditional and do not use `if`,
   because with `if` the rewriter unfolds the search on a symbolic set without end
   (checked on 2026-10-07: `mcrl22lps` was killed on the `if` form and linearised the
   conditional form, and `lps2lts` then produced `pick(job(3))`, `pick(job(7))`,
   `pick(job(12))` from `{job(7), job(3), job(12)}`). For an index of order `least`
   or `key`, `first(I)` and `some(I)` are such searches over the ids
   `0 … bound − 1`, with the parameters the index reads passed as arguments; for an
   index of order `newest` or `oldest`, they are walks over the index's list (3); for an index of order `within` (4), walks over
   the list of the index it is within.
3. For every index of order `newest` or `oldest`, a parameter `ix_<name>: List(I)`,
   initially `[]`, holding the members in index order (newest first, or oldest
   first), and, in every summand that writes a parameter the index's predicate or
   class reads, the assignment that keeps it. For each id `y` the summand writes,
   with `a` the membership of `y` before and `b` after the summand, it is
   `ix_<name> = if(b && !a, <insert y>, if(a && !b, <remove y>, ix_<name>))`,
   with insertion at the front (`newest`) or at the end (`oldest`). The ids a summand
   writes must be visible in its assignments: function updates `f[x -> v]`, possibly
   under `if`, and `+ {x}`, `- {x}` on a set. A parameter read by such an index and
   written in any other way (by a walk, by a whole-set operation) is G13.

4. For an index `I` of order `within`, `within = "J"`, no parameter: `first(I)` and
   `some(I)` walk `ix_J`, the list of `J` (item 3), and stop at the first id at
   which `I`'s membership predicate holds and its class is admitted. As `I`'s
   predicate has `J`'s as a conjunct (§3.7.6), every member of `I` is in `ix_J`,
   and the walk sees `I`'s members in `J`'s order. Nothing keeps a list for `I`,
   so a summand may write what `I`'s predicate reads by any operation (by a walk,
   by a whole-set operation): only `J`'s predicate is held to item 3.

The refined summands are printed from the generator's internal form, with the
recovered names of §3.5.3, in policy order for outputs and source order for inputs.
The names `ix_*` are reserved for the generator; a model or policy name that starts
with `ix_` is G9.

#### 3.7.5 What is generated from what

The stepper is generated from the LPS of the refined Core (§3.2.4), with one
exception: the reference definitions of §3.7.4 items 2 and 3 are never compiled. The
generator recognises them by their reserved names and emits, in their place, the
index implementations of §3.10.3, exactly as it emits a catalogue entry's
implementation instead of its equations (§3.4.7). The acceptor is generated from the
LPS of the unrefined Core, which has no index. Both are generated in every backend.

#### 3.7.6 Selection operations and indexes

Three operations may appear in a selector term, and nowhere else:

- `least(W)`, with `W` an `FSet(I)` parameter of an id sort `I`: the element of `W`
  with the least index. Defined when `W != {}`. A worklist read by `least` gets the
  representation *ordered bitset* (§3.10.3).
- `first(J)`, with `J` an index: the first member of `J` in `J`'s order whose class
  `J` admits. Defined when `some(J)`.
- `some(J)`: whether `J` has a member whose class `J` admits.

An index `[index.<name>]` declares:

- `sort`: the id sort `I` of its members; `var`: the name of the member variable in
  the terms below; `bound`: a term over the parameters bounding the ids that can be
  members (`nj` for `Job`). An id at or above it is never a member, an invariant the
  search of §3.7.4 relies on and §3.9.9 checks with the rest.
- `member`: a Boolean term, the membership predicate. It is local: it reads
  only columns applied to the member variable itself (`st(n)`, never
  `st(next(n))`), its membership in frontier sets (`n in W`), constructors and
  constants. It reads no scalar parameter, no column at another id, no quantifier and
  no walk. Whatever a local predicate reads changes at a known id when it changes,
  and only that id needs to be looked at again. A predicate that is not local is G9.
- `order`: `least` (least id first), `key` (least value of the term `key` first, a
  local term of an integer sort; ties by least id; a key that is not one column read
  at the member variable gives its sort in `key_sort`, `Nat` or `Int`, else G9), `newest` (the member that became
  a member most recently first), `oldest` (the reverse), or `within` (the order of
  the index named by `within`). "Became a member" is the moment the predicate turned
  true at that id; an id that stays a member keeps its place.
- `within`: given exactly when the order is `within`; the name of an index `J` of
  the same sort and bound, of order `newest` or `oldest`, whose membership predicate
  is a top-level conjunct of this index's (written first: `member = "<J's member>
  && ..."`), so that every member of this index is a member of `J`. The members are
  then ordered as in `J`: for `J` of order `newest`, the member that became a member
  of `J` most recently first. Anything else is G9.
- `class` and `admits`: optional, together. `class` is a local term of an
  enumeration sort `K`; `admits = { var = "k", when = "<Boolean term>" }` is a
  condition on the class `k` over scalar and table parameters only (no column).
  `first(J)` is then the first member, in `J`'s order, whose class is admitted.

The membership, order and class of every index are a function of the state (and,
for `newest` and `oldest`, of the history of that function), so `first` and `some`
are functions of the refined Core's state. The refined system is the system the code
runs, and the model checker sees it whole.

#### 3.7.7 The policy of the reference model

Left to the project. A project's policy, its choices by kind and why each selector
is head-enabled or deferred, belong with its model.

#### 3.7.8 The order rule of the requirements

Left to the project: how its own requirements on order are met by the indexes of
its policy.

### 3.8 What the generated code means

#### 3.8.1 The constructor

`new(config)` builds the state `s0`: every parameter takes its value from
`<model>/codegen-init.mcrl2` (or from its `init`, §3.6.2), except configuration
parameters, which take theirs from `config`. It then runs the output loop (§3.8.3)
from `s0` and returns the resulting state and the actions emitted, usually none.

#### 3.8.2 The step

`step(s, e)`, for an event `e` with label `c_x` and argument values `v̄`:

1. Among the input summands with label `c_x`, find the one that accepts `v̄`: bind each
   sum variable that is a whole action argument to the value at its position, compute
   the equality-fixed variables, evaluate the remaining action arguments and compare
   them with the values at their positions, and evaluate the condition. By §3.7.3 at
   most one summand accepts.
2. If there is none, the step fails with `Refused(e)` and the state is unchanged.
3. Otherwise `s1` is the next state of that summand.
4. Run the output loop from `s1` and return the final state and the actions emitted,
   in order.

The conjunct `Q` of §3.7.2.3 is not evaluated, because the state at the start of
every step is quiescent: the constructor and every step end with the output loop.
Debug builds evaluate it and fail with `NotQuiescent` if it is false.

#### 3.8.3 The output loop

Repeat: find the first output summand, in policy order, whose condition `c'_j` holds;
if there is none, stop; otherwise append its action, with its arguments evaluated in
the current state, to the result, and move to its next state. Trying the summands in
order is what makes the priority conjuncts of §3.7.2.2 true without evaluating them.
The loop stops with error `StepLimit` after `2^24` actions in one step. A correct
model never reaches it, since every output-only path is finite on the reference
instances (§3.9.5), and reaching it is reported, never truncated.

After every summand it fires, input or output, and before the next condition is
evaluated, the code refreshes the indexes. Each assignment to a parameter that an
index's predicate, class or key reads records the ids it writes (a function update's
id, a set's inserted or removed element, each id a compiled walk writes) in a
per-step list of dirty ids. Once all the summand's assignments are done (§3.8.4), the
predicate, class and key of each affected index are evaluated at each dirty id in the
new state, and the index is updated where they changed. So every condition, and every
`first` and `some` in it, reads indexes that match the state, as the reference
definitions of §3.7.4 do. A `newest` or `oldest` index records the entry moment as a
stamp, a 64-bit counter of entries taken from the state (§3.10.3); an id that stays a
member keeps its stamp, including across a change of class.

#### 3.8.4 Simultaneous assignment

Every right side of a summand's next-state assignments, and every action argument,
is evaluated in the state before the summand fires, as in the LPS. The generated
code writes in place only where that gives the same result; otherwise it evaluates
into temporaries first. A function update `f[i -> v]` is a write of one array slot.
A set or list whose new value is built from its old one (`S - {x}`, `S + T`, a walk
that takes `S` as its accumulator, under `if`) is not copied: once every other right
side of the summand that reads it has been evaluated, the old value is moved into the
expression that builds the new one, along a path of `if` branches and consumed
operands on which nothing reads it after the move (a right operand that reads it is
evaluated first). A walk or map moves its own set and list arguments the same way.
The set operations `+`, `-` and `*` of two owned sets update the larger one in place,
so their cost is that of the smaller. A set whose old value is needed after the step
(a parameter an index reads, whose dirty ids are its difference with the new value)
is still copied; §3.13.1 counts that copy.

#### 3.8.5 Errors during a step

A refused event leaves the state unchanged (§3.8.2.2). Any other error during a step
(§3.14.2) ends the run: the error is returned with the summand being fired and the
actions emitted so far in the step, and the state must not be stepped again.

#### 3.8.6 Determinism

The result of `new` and of `step` is a function of their arguments. No clock, random
number, environment variable, hash iteration order, memory address or thread identity
enters it. If the model needs time, time arrives as an argument of an input (a tick
carrying the clock reading). The same event sequence therefore gives the same action
sequence in every backend and in every run, which is what lets a recorded run be
replayed (§3.16).

#### 3.8.7 Sans-I/O, one thread, no locks

The stepper and the acceptor read and write no file, socket, database or terminal,
start no thread, take no lock, call nothing outside the generated code and the
catalogue, and wait for nothing. Everything they want done leaves as an action, and
everything they learn arrives as an event. One state is used by one thread at a
time; the caller owns it, and the generated code never shares it. This is what makes
the generated code unable to stall on input or output.

#### 3.8.8 What the generated code does not hold

The generated code holds only what the model's parameters hold: ids, counts, sizes,
states and collections of these. Data the component's decisions do not depend on
(payloads, handles, file paths, messages) stays with the caller, keyed by id, and an
event carries only the arguments of its label. An action whose request needs such
data carries ids only, and the caller adds the rest when it carries the action out.

#### 3.8.9 The acceptor

`accept(s, entry)` checks one entry of the action record (an event or an action)
against the unrefined Core:

1. Among the summands with the entry's label, bind each sum variable that is a whole
   action argument to the entry's value at that position, compute the
   equality-fixed variables, evaluate the remaining action arguments and compare them
   with the entry's values, and evaluate the condition.
2. If exactly one summand passes, the acceptor's state becomes its next state.
3. If none passes, `accept` fails with `Rejected(entry)`; if two pass (which §3.9.6
   excludes on the reference instances), with `Ambiguous(entry)`.

The acceptor does not run an output loop and does not apply the policy: it accepts
every step the model allows. Its state is its own; it never reads the stepper's. A
runtime monitor, which is the caller's, feeds it the record in sequence order, and a
gap in sequence numbers is the monitor's to detect.

#### 3.8.10 The action record

The generated code does not write the record. The caller appends the event, then
each action `step` returned, in order, with consecutive sequence numbers. The
generator emits, in every backend, the encoding and decoding of events and actions to
and from entries, from the `act` declarations, so that the record's vocabulary is the
model's by construction.

### 3.9 Checks that the refinement stays inside the model

Every check runs on every group of reference instances (§3.17), on the systems the
generator assembles under `<generated>/model/systems/` (§3.9.7).
`naima-codegen check-refinement` runs them (§3.6.9): for each group it linearises and
explores the three systems with `mcrl22lps` and `lps2lts`, decides the checks of
§3.9.2, §3.9.3, §3.9.5, §3.9.6 and §3.9.7 on the explored state spaces, and prints one
line per check; it exits with status 1 when any fails. `<generated>/model/probe.mcf`
holds the same checks as modal formulas, for a project that verifies them with
`lps2pbes` and `pbessolve`, or records them as property items with evidence.

#### 3.9.1 By construction

The generator asserts, before emitting code, that every refined output summand has
no sum variable, and that every sum variable of a refined input summand is a whole
action argument or equality-fixed. Failing either is a generator defect (error G10).

#### 3.9.2 Trace inclusion

For each group, both systems (with Core and with the refined Core) are explored with
`lps2lts` and compared with

```sh
ltscompare --preorder=trace-ac --tau=<observer labels> refined.aut model.aut
```

which must report inclusion. The observer labels are every label `o_*` declared in
`<model>/observers.mcrl2`. They are hidden because the refined system carries the
observers and the model system does not (§3.9.7). `--no-model` skips this check,
for a group whose model system is too large to explore.

Checked on 2026-10-06 on a model of four jobs in a diamond with two workers: a Core
that may start any ready job and a refined one that starts the least ready job with
outputs before inputs gave 48 and 37 states; the refined system is included in the
model and not the reverse.

Trace inclusion alone is satisfied by a refinement that does nothing; §3.9.3 to
§3.9.5 exclude that.

#### 3.9.3 Non-blocking

The probe Core (`<generated>/model/core-probe.mcrl2`) is the refined Core plus, for
each output summand `j` of the unrefined Core, a summand
`sum x̄ . c_j -> probe_enabled . Core()`, and one summand
`Q -> probe_quiet . Core()`. Both leave the state unchanged, so the reachable states
are those of the refined system. The property

```
[true*] !(<probe_enabled>true && <probe_quiet>true)
```

says that in no reachable state does the model allow an output while the refined Core
has none. Checked on 2026-10-06 on the diamond: true for the least-ready policy,
false for a policy that starts a job only when the last one is ready.

A deferred summand (§3.7.1) has no `probe_enabled` summand. The property then says
that the refined Core is never idle where the model allows a non-deferred output.
That the deferred summand's work is still done is carried by §3.9.4, which
re-verifies on the refined system every liveness property of the model (a summand
deferred forever breaks the property that says its work is eventually done). The
generator writes, for each deferred summand, a probe summand `probe_deferred_<j>`,
and the check reports, without failing, the number of reachable states in which the
summand is deferred, so a policy that defers more than it says shows in the report.

#### 3.9.4 The properties hold on the refined system

Every property item of the project (§3.6.9) is verified on the refined system of the
group its `meta.json` names, in addition to the system with Core.
`naima-codegen check-refinement --properties` does it with `lps2pbes` and
`pbessolve`. Trace inclusion carries safety properties to the refinement but not
liveness: a policy can starve an item the model would eventually serve, and only this
check sees it. The refined systems carry the observers of `<model>/observers.mcrl2`,
and the check also reports any observer transition on a refined system as a failure.

#### 3.9.5 Every step ends

On the refined system, with `Out` the set of vocabulary names of the output labels:

```
[true*] mu X . [Out] X
```

says that from no reachable state is there an infinite path of outputs alone, so the
output loop of §3.8.3 ends. On an explored state space this is the absence of a cycle
of output transitions.

#### 3.9.6 Labels select one summand

For each pair of unrefined output summands `i`, `j` that share a label, the probe Core
has a summand `sum x̄_i, x̄_j . c_i && c_j && ē_i == ē_j -> probe_ambiguous . Core()`,
where `ē` are the action arguments; the property is
`[true*] [probe_ambiguous] false`. For two input summands of one label the probe
summand is `c_x_ambiguous(x̄)`, composed with the environment as in §3.6.8, so the
environment supplies the arguments and no probe enumerates them; the property is
`[true*] [exists v̄ . ambiguous_x(v̄)] false`.

#### 3.9.7 Every event the environment can send is accepted

For each input label `c_x`, the probe Core has a summand
`sum x̄ . Q && !(c_x,1 || ... || c_x,r) -> c_x_refused(x̄) . Core()`, where
`c_x,1 … c_x,r` are the conditions of the input summands with that label. Composed with
the environment by `c_x_refused | e_x -> refused_x` (§3.6.8), a `refused_x` transition
exists exactly where the environment offers an event that the quiescent Core would
refuse. The property is `[true*] [exists v̄ . refused_x(v̄)] false` for every `x`. In
the model a refused offer just waits, by synchronisation; in the real system the
event arrives and the step fails with `Refused`.

The generator writes these properties to `<generated>/model/probe.mcf` and, for each
group `<group>` of instances (§3.17), the three systems
`<generated>/model/systems/<group>-{model,refined,probe}.mcrl2`, each the
concatenation of §3.1.1 with the matching Core. The model system has Core as written
and no observers; the refined system has the refined Core and the observers of
`<model>/observers.mcrl2`; in the probe system, the instances' `comm` and `allow` are
extended with the probe labels of §3.6.8.

#### 3.9.8 At run time

On every run, a monitor can replay the record through the acceptor (§3.8.9). Since
the stepper is a refinement of the model, every entry it produced must be accepted. A
rejection is a defect of the generator, of a catalogue implementation, of the record
writer or of the caller (which sent an event the model does not allow), and fails the
run. The acceptor of one backend may check a record written with the other, which
cross-checks the two generated codes on every such run.

#### 3.9.9 The index audit

The model checker verifies the refined Core with the reference definitions of the
selection operations (§3.7.4); the code runs their implementations (§3.10.3). Three
checks join them.

1. Audit after every event. The Python backend (§3.12) has an audit mode
   (`State(config, out, audit=True)`), used by the tests of §3.16 and never by a run. After every
   summand it recomputes each index from its definition (a scan of the ids below
   `bound`, and for `newest` and `oldest` the order kept beside it as a list, as
   §3.7.4 item 3 keeps it, and for `within`
   that list of the index it is within, filtered by its predicate) and compares it with the implementation, membership,
   order and class; and it checks that no id at or above `bound` is a member. A
   difference fails the test with the index, the event and the id. The Rust and
   TypeScript backends are compared with it after every event (§3.16.1), so they are
   audited through it; the TypeScript stepper's state space is also compared with the
   refined Core's, reference definitions included (§3.18.5).
2. Replay in the refined LPS. Seeded random walks of the refined systems, explored by
   `lps2lts` and projected to events, fed to the Python stepper in audit mode give exactly the
   actions of the walk, and the acceptor accepts every entry. A test does this at
   every change of the model directory, the catalogue or the generated directory.
3. At run time, the acceptor of §3.9.8 rejects any selection the model forbids.
   It cannot see an order the policy got wrong but the model allows, which 1 and 2
   see.

### 3.10 The data-structure catalogue

#### 3.10.1 What an entry is

An entry is a directory `<catalogue>/<entry>/` with:

- `reference.mcrl2`: the sort and its operations as mCRL2 maps and equations, copied
  verbatim into `<model>/core.mcrl2` when the model uses the entry (§3.3.6). Entries
  over mCRL2's built-in sorts (lists, finite sets and bags, function sorts) have no
  reference file, since mCRL2's own semantics is the reference.
- `ops.toml`: per operation, its mCRL2 name and signature, its cost class, and the
  Rust and Python code the generator emits for it.
- `rust.rs` and `python.py`: the implementations, written by hand, reviewed, and not
  generated.
- a conformance test: seeded random sequences of operations, at least 10,000
  operations per entry in sequences of up to 200, each evaluated by `mcrl2i` on the
  reference (checked on 2026-10-06: `mcrl2i` evaluates the priority-queue reference
  and returned the expected top after three pushes) and by each implementation.
  Every result must be equal. It runs on every commit that changes the catalogue.

An entry over a built-in sort has `ops.toml`, the two implementations and the
conformance test, which `mcrl2i` evaluates on mCRL2's own built-in semantics; it has
no `reference.mcrl2` and nothing for G7 to compare. User maps, walks included
(§3.4.8), are never catalogue entries: the generator compiles them.

Not implemented yet: `ops.toml`, the hand-written implementations and the
conformance tests. The generator reads `reference.mcrl2` only, for the check G7.

#### 3.10.2 Default values

`false`; `0` for `Nat`, `Int` and the field of an id sort, `1` for `Pos`; the first
constructor of an enumeration or union, with default fields; a record of defaults;
the empty collection. They fill a column beyond the ids written so far and the
parameters of §3.2.5.

#### 3.10.3 The entries

| entry | mCRL2 form | operations and cost class | Rust | Python |
|---|---|---|---|---|
| boolean | `Bool` | all `O(1)` | `bool` | `bool` |
| integer | `Nat`, `Pos`, `Int` with a width | all `O(1)` | `u8 … u64`, `i32`, `i64`; arithmetic checked (§3.12.4) | `int`, range-checked at every write (§3.12.4) |
| enumeration | `struct a \| b \| …` | `O(1)` | `#[repr(u8)] enum` | `int` constants `0 … n-1` |
| record | `struct c(f1: …, …)` | `O(1)` | `struct`, `Copy` when its fields are | `tuple`; in a column, one column per field |
| union | `struct c1(…) \| c2(…)` | `O(1)` | `enum` with fields | `tuple` whose first element is the constructor's number |
| id | id sort | `O(1)` | `u32` newtype | `int` |
| column | `I -> X`, `X` scalar or record | read `O(1)`; update `O(1)` amortised | `Vec<X>` per field, grown to the largest id written, filled with the default; a read beyond the end gives the default | `array.array` of the width's type code (`B H I Q i q`), grown by doubling |
| table | `E -> X`, `E` an enumeration | `O(1)` | fixed-size array | `list` of fixed length |
| hash map | `K -> X`, other `K` | read, update `O(1)`; never iterated; writing the default removes the key | `HashMap` with a fixed hasher (no random seed) | `dict` |
| bitset | `FSet(I)`, `repr = "bitset"` | `in`, insert `+ {x}`, remove `- {x}`, `#`, `== {}`: `O(1)` | `Vec<u64>` and a count | `bytearray`, one byte per id, and a count |
| ordered bitset | `FSet(I)` read by `least` (§3.7.6), `repr = "ordbitset"` | `in`, `#`, `== {}`: `O(1)`; insert, remove: `O(log F)`; `least`: `O(log F)` | the bitset and a `BTreeSet<u32>` of the members | the `bytearray` and a `heapq` of the members with lazy deletion (an id popped is discarded unless its byte is set), compacted when stale entries outnumber members |
Until the whole rule is implemented, the generator applies one part of it to the
Rust it emits: code of a summand (its acceptor and stepper functions) that copies
a whole frontier set or list (a parameter of a set or list sort, not a column) or
scans one (iterates it, or its difference with another) is G8, unless `cost_known`
in the declaration (§3.6), a list of `"<summand>: <copy | scan> <parameter>"`,
names it; an entry that no longer happens is G8 too, so the list only shrinks. The
manifest lists each summand's entries under `cost`.

| index | `[index.<name>]` of the policy (§3.7.6) | refresh at one id, `first`, `some`: `O(log F)` amortised, times the number of classes for `first` and `some` of a classed index; `F` the number of members | per index, a membership bitset (one bit per id); per class, a `BTreeSet` of `(order key, id)`, the order key being the id (`least`), the key's value (`key`) or the stamp, reversed for `newest`; for `newest` and `oldest`, a column of `u64` stamps and a run-level counter. Entries are deleted lazily: an entry is valid while its id is a member, of that class, with that key or stamp; invalid ones are dropped when they reach the front, and the set is rebuilt from its valid entries when invalid ones outnumber them | the same with `heapq` per class and `array('Q')` stamps |
| hash set | `FSet(X)`, default | `in`, insert, remove, `#`, `== {}`: `O(1)`; `+ - * <=` of two sets: `O(k)`, only on per-id sets | `HashSet` with a fixed hasher | `set` |
| bag | `FBag(X)` | `count`, add one, remove one: `O(1)` | `HashMap<X, u32>` | `dict` |
| stack | `List(X)` used only with `[] \|> head tail # == []` | `O(1)` | `Vec`, top at the end | `list`, top at the end |
| queue | `List(X)` used with `<\|` | `[] <\| \|> head tail rhead rtail # == []`: `O(1)` | `VecDeque` | `collections.deque` |
| per-id list, fixed | column of `List(I)`, `repr = "adj-fixed"`, written once per id | write `O(k)`; iterate, `#`, `in`, `.`: `O(k)` | one shared `Vec<u32>` and, per id, start and length (`u32` each) | the same in `array('I')` |
| per-id list, growing | column of `List(I)` or `FSet(I)`, `repr = "adj-append"` | append `O(1)` (for a set, insert-if-absent `O(k)`); iterate, `#`, `in`: `O(k)` | shared pool of (value, next) pairs, per id head, tail and length | the same in `array('I')` |
| priority queue | catalogue sort `PQueue` of `e(prio: Int, seq: Nat, id: I)`, larger `prio` first, then larger `seq` | `pq_push`, `pq_pop`: `O(log F)`; `pq_top`, `pq_isempty`, `pq_size`: `O(1)`; `pq_remove(q, id)`: `O(log F)` amortised (lazy deletion) | `BinaryHeap<(i64, u64, u32)>` | `heapq` on `(-prio, -seq, id)` |
| ordered set | catalogue sort `OrdSet` of `o(key: Nat, id: I)`, least first | `os_insert`, `os_remove`, `os_min`: `O(log F)`; `os_member`, `os_size`: `O(1)` | `BTreeSet<(u64, u32)>` and a count | `heapq` with lazy deletion and a membership `set` |

The priority queue and the ordered set are for a model that keeps such a structure
in its own state. A model that leaves an order to the policy gets an index instead,
whose implementation is the *index* entry. The reference of `PQueue` is the sorted
list of the test of §3.10.1. The ordered bitset and the index have no reference file.
Their reference is the definition the generator writes into the refined Core
(§3.7.4), and their conformance test (§3.10.1) runs random operation sequences
against those definitions evaluated by `lps2lts` on a one-summand process, as the
check of 2026-10-07 quoted in §3.7.4 did.

The TypeScript backend's representations are its own (§3.18.2).

Not implemented yet: the representations of this table other than the index. The Rust
and Python backends represent values generically and correctly but not at the costs above. In
Python a function-sorted parameter is a default plus a `dict` of entries, a list is
a tuple and a finite set a `frozenset`; in Rust every integer is an `i128` and
functions and sets are ordered maps and sets of `std::collections`. The index is
implemented with ordered sets and lazy deletion in Rust and with `heapq` in Python,
with stamps for `newest` and `oldest`; `least` is a search of the set.

#### 3.10.4 Choosing the representation

The representation follows from the parameter's sort and role and from `repr` in
`codegen.toml`. A function-sorted parameter over an id sort is a column (a per-id
list when its values are lists or sets and `repr` says so); a `List` frontier
parameter is a stack when every operation Core applies to it is a stack operation,
and a queue otherwise; an `FSet` parameter is a hash set unless `repr` is `bitset`,
and an ordered bitset when the policy reads it with `least` (§3.7.6), whatever `repr`
says. An operation that the chosen representation does not provide within the cost
class allowed where it occurs (§3.13.1) is error G8: the generator never falls back
to a slower structure on its own.

Not implemented yet: the choice and its G8. `repr` is read and checked against the
sort, and has no effect on the generated code.

#### 3.10.5 Iteration order never shows

No implementation's iteration order reaches an action or a state. Hash maps and hash
sets are never iterated, quantifiers over per-id collections produce Booleans, and
every selector uses an order fixed by its entry or index (§3.7.1, §3.7.6). An index
breaks every tie by the least id, and its stamps count entries in the order the
summands make them, which every backend follows. This is what lets the backends,
with different hash functions, take the same actions.

#### 3.10.6 Sorted per-id lists

A model may keep, per id, a list of ids as a set ordered by id: it starts empty and
grows only through an *ordered insert*, a user map whose equations are exactly

    f(u, []) = [u];
    f(u, n |> l) = if(u == n, n |> l, if(p(u) < p(n), u |> (n |> l), n |> f(u, l)));

(`p` the one field of the id sort, §3.3.3), or an *ordered merge* through it:

    g([], l2) = l2;
    g(n |> l, l2) = g(l, f(n, l2));

The generator recognises both from their equations (variable names aside). A column
`P: I -> List(I)` is *sorted* when its initial value is the constant `[]` and the new
value every summand gives it is, on each path of its `if`s, `P` itself, `P` with one
entry `k` updated to `f(u, P(k))` or `g(l, P(k))`, or a call of a *sorted walk* with `P`
at its function-sorted result's position and nowhere else. A sorted walk (§3.4.8) is
one whose every step leaves that parameter in the same form, and which is called
nowhere but there. These conditions are a greatest fixpoint, taken over the summands
of the Core and of the stepper Core. By induction on the run, every value of a sorted
column is sorted by id, and on a sorted list `f(u, l)` is `l` with `u` at the place a
binary search finds (unchanged when `u` is in it).

The Rust backend applies such an update in place: the summand's other new values are
computed first (all of them that read the column, which must not include a set or
list moved or changed in place, §3.8.4), then the entry is changed by a binary search
and an insert (`O(log k)` to find the place, and an append when the id is the
greatest, as it is when ids are made in increasing order), and a sorted walk is given
the column itself. The walk the model defines (`O(k)`, and a copy of the list) is
emitted too and is used everywhere else.

The Python backend, whose lists are tuples, finds the place by a binary search and
builds the new tuple by slicing (a copy, made by the interpreter rather than by a walk
of the list). In the oracle's audit mode (§3.9.9) every such insert and merge is also
computed by the model's walk, and a difference is an audit failure, so the
cross-check of §3.16 compares the Rust code with the model's own definition.

### 3.11 The Rust backend

#### 3.11.1 The crate

`<generated>/rust/<rust_crate>` is a Cargo package named `rust_package` (§3.6.9). Its
`Cargo.toml` inherits the workspace's fields and lints when `rust_workspace` is true
and is standalone otherwise. It depends only on `std`, on the catalogue's Rust crate
and, behind the feature `python`, on PyO3. It declares `#![forbid(unsafe_code)]`. A
test scans the generated sources and the catalogue crate and fails on any use of
`std::fs`, `std::io`, `std::net`, `std::thread`, `std::sync`, `std::time`,
`std::env`, `std::process` or an external crate other than these.

Not implemented yet: the dependency on a catalogue crate and the feature `python`;
the crate depends on `std` only.

#### 3.11.2 The interface

```rust
pub struct Config { /* one public field per configuration parameter */ }
pub struct State  { /* private */ }
pub enum Event  { /* one variant per input label, named after the vocabulary name, fields from `args` */ }
pub enum Action { /* one variant per output label */ }
pub enum Entry  { Event(Event), Action(Action) }

impl State {
    pub fn new(config: Config, out: &mut Vec<Action>) -> Result<State, StepError>;
    pub fn step(&mut self, event: &Event, out: &mut Vec<Action>) -> Result<(), StepError>;
    pub fn bytes(&self) -> usize;               // memory held by the state
    // one read-only method per observable (§3.6.7)
}
pub struct Acceptor { /* private */ }
impl Acceptor {
    pub fn new(config: Config) -> Acceptor;
    pub fn accept(&mut self, entry: &Entry) -> Result<(), Reject>;
}
pub fn encode(entry: &Entry, buf: &mut Vec<u8>);
pub fn decode(bytes: &[u8]) -> Result<Entry, DecodeError>;
```

Actions are appended to `out`; the caller clears and reuses it, so a step allocates
nothing in the common case.

This is the interface a Rust caller uses: it depends on the crate without the
feature `python`, builds the `Config`, calls `State::new` and `step`, feeds an
`Acceptor` in its runtime monitor and frames the bytes of `encode` in its action
record. No Python object and no PyO3 type appears in that use of the crate.

Not implemented yet: the observable methods (§3.6.7). `bytes()` is an estimate.

#### 3.11.3 No panic

The generated code has no path that panics: no `unwrap`, `expect`, `panic!`,
`unreachable!`, no slice indexing (reads go through the catalogue's accessors, which
give the default beyond the end), no arithmetic that can wrap. It is compiled with
`clippy::unwrap_used`, `clippy::expect_used`, `clippy::panic`,
`clippy::indexing_slicing` and `clippy::arithmetic_side_effects` denied. Every failure
is a `StepError` (§3.14.2).

#### 3.11.4 The Python binding

`src/py.rs`, under the feature `python`, builds an extension module that presents
exactly the Python interface of §3.12.2: the same classes, methods, event and action
tuples and exception classes. It exists for the tests of §3.16, which drive the Rust
and the Python backends from one Python harness with the same tuples. The module is
declared free-threading compatible (`#[pymodule(gil_used = false)]`); a second call
on the same state from another thread while one is running raises an error from
PyO3's borrow check instead of racing.

Not implemented yet: the binding. The tests of §3.16 compile the crate with a small
native driver instead (§3.16.5).

### 3.12 The Python backend

The Python backend is a target a project chooses like the others (§3.6.9). It is
generated from the same refined Core, checked by the same diff check (§3.15) and the
same rules (§3.13), checked against mCRL2 itself (its maps against the rewriter, its
stepper's state space against the refined Core's, §3.16.8), and compared with the
other backends (§3.16). It keeps the simplest representations of §3.10.3 that meet
the semantics (a stored function as a default and a `dict` of entries, lists as
tuples, sets as `frozenset`s, §3.12.3) as its own choice: they make it the backend
whose code is easiest to read next to the model, and its costs are its own, measured
as §3.13.2 says.

#### 3.12.1 The module

`<generated>/python/<python_package>/__init__.py` imports only `array`, `heapq`,
`collections` and the catalogue package built from `<catalogue>/*/python.py`. A test
over its imports, and over the catalogue's, fails on anything else (in particular
`os`, `io`, `socket`, `threading`, `time`, `random`, `sqlite3`, `asyncio` and
`numpy`). It runs on free-threaded CPython; it takes no lock, and the uncontended
per-object locking of free-threaded builtins is its only synchronisation.

#### 3.12.2 The interface

```python
EV_<NAME>: int    # one constant per input label
ACT_<NAME>: int   # one constant per output label

class Config:     # __slots__: one per configuration parameter
class State:
    def __init__(self, config: Config, out: list, audit: bool = False) -> None   # appends the initial actions
    def step(self, event: tuple, out: list) -> None          # appends the actions
    def bytes(self) -> int
    # one method per observable
class Acceptor:
    def __init__(self, config: Config) -> None
    def accept(self, entry: tuple) -> None
class StepError(Exception)        # subclasses Refused, Overflow, Undefined, StepLimit, NotQuiescent
class Reject(Exception)           # subclasses Rejected, Ambiguous
def encode(entry: tuple) -> bytes
def decode(data: bytes) -> tuple
```

An event or action is a tuple `(code, arg1, …, argm)`; an entry is `(0, event)` or
`(1, action)`. Tuples, not objects, because one is built per event and per action.
`audit` turns on the index audit of §3.9.9.

Not implemented yet: the observable methods (§3.6.7).

#### 3.12.3 Layout

The state's attributes are `__slots__`. A column is a default plus a `dict` of the
entries that differ from it, a list a tuple and a finite set a `frozenset`: the
backend's representation (§3.12). `step` dispatches on the event code through a
table indexed by it. Each summand is straight-line code; the only loops are the
output loop and loops over a per-id collection or a list argument.

Not implemented yet: columns as `array.array` of the declared width, so that an id
costs its columns' bytes and no Python object, for a project whose memory budget
(§3.13.4) needs it.

#### 3.12.4 Integers behave as in Rust

Every backend checks that every value written to a parameter, a column slot or an
action argument fits its declared width, and fails with `Overflow` at the same write
if it does not. Intermediate results are computed exactly in Python, in `i128` with
checked operations in Rust, and as safe integers in TypeScript (§3.18.2). The
generator computes, from the declared widths of the operands, the range of every
intermediate and refuses (error G8) an expression whose intermediates could leave the
`i128` range, or, for a project that chooses TypeScript, the range of safe integers,
so the backends agree on every input.

Not implemented yet: the range analysis of intermediates and its G8. Until it is, a
TypeScript intermediate beyond 2^53 - 1 in magnitude is `Overflow` where Rust and
Python compute it exactly.

### 3.13 Cost and memory budgets

#### 3.13.1 The static rule

Every occurrence of an operation in a summand has a cost class, from `ops.toml` and
the representation (§3.10.3):

- `O(1)`;
- `O(log F)` on a frontier structure;
- `O(k)` on a per-id collection of an id that the summand obtains from an event
  argument, a sum variable or a value read in the same summand, or on a list action
  argument;
- `O(k²)` inside a walk only (§3.4.8): a walk over such a collection or list whose
  body costs `O(k)` on another;
- `O(k · log F)`: a walk or a per-id collection operation of cost `O(k)` that
  writes a parameter read by an index or an ordered bitset, each write then costing
  an `O(log F)` refresh (§3.8.3).

A selector costs what its operations cost: `least(W)`, `first(J)` and `some(J)` are
`O(log F)` amortised (times the classes of `J`, a constant of the model), and a map of
the policy's `maps` costs as a walk. The refresh after a summand costs `O(log F)` per
dirty id and index, and a summand has at most a constant number of dirty ids per
function update, or `k` per walk.

Any operation whose cost grows with the number of ids is refused (error G8):
iterating, comparing or copying a column or a frontier structure as a whole, `in` or
`.` on a frontier list, a quantifier not bounded by a per-id collection. Each
iteration of the output loop evaluates at most `m` conditions, `m` being the number
of output summands, a constant of the model. A step that emits `r` actions therefore
costs at most a constant times `m · (r + 1) · (1 + k) · (1 + log F)`, where `r`
depends on the model and not on the number of ids.

Not implemented yet: cost classes and their G8. The front end refuses unbounded
quantifiers (G3, §3.4.5) and nothing else on cost grounds, and the manifest's
`cost` of every summand is null.

#### 3.13.2 CPU per unit of work

Left to the project: a budget of CPU time per unit of work in each backend, measured
by replaying a recorded event sequence through `new` and `step` on one thread with no
I/O.

#### 3.13.3 Independence from the number of ids

Left to the project: a check that the time per event does not grow with the number
of ids, measured on synthetic event sequences of two very different sizes.

#### 3.13.4 Memory per id

The generator computes the per-id layout of each backend (the bytes of every column,
plus the per-entry bytes of per-id lists shown separately) and writes it to the
manifest (`layout`). Frontier structures are counted in `bytes()` but are bounded by
the frontier rather than by the number of ids. The indexes count in the columns: one
bit per id for each index's membership, one bit per id for each ordered bitset (as
for a bitset), and 8 bytes per id for each `newest` or `oldest` index's stamps. The
limit in bytes per id, which G11 enforces on the columns, and its measurement on a
large run are the project's.

The layout is computed from the representations of §3.10.3, whatever the backends
emit today: a column takes one value of its sort at its width (§3.6.6; `Bool` and an
enumeration one byte, an id sort `u32`, a record the sum of its fields, a union a tag
byte and its largest constructor), a column of lists or sets eight bytes (start and
length) plus the entries' bytes, listed under `entries`; bits are rounded up to whole
bytes. The manifest's `layout` has a table per backend (the same today) and per id sort:
`columns`, `entries`, `index_bits`, `stamps` and the total `bytes`. The project's limit
is `bytes_per_id` in the declaration, a table of id sorts to bytes; a total over it is
G11.

#### 3.13.5 Generation time

Left to the project: a bound on the time of generation and of the diff check of
§3.15, without the model checks of §3.9, small enough for the check to run at every
commit.

### 3.14 Errors

#### 3.14.1 Errors of the generator

Every error names the file, the summand or construct, and the section of this
specification it breaks. The front end collects every error it finds before it
refuses, so that one run lists them all. The generator writes its outputs to a
temporary directory and moves them into `<generated>` only when every step has
succeeded, so on any error `<generated>` is left as it was.

| error | when |
|---|---|
| G1 | the mCRL2 toolset is missing or its version differs from the pin (§3.2.1) |
| G2 | `mcrl22lps` or `lpspp` failed; its message is passed through |
| G3 | a map, equation, constant, quantifier or function-sorted argument outside §3.4 (§3.4.8 included) |
| G4 | Core is not a linear process, its parameters differ from the declaration, a label does not start with `c_`, a sum variable has a parameter's name, or the LPS order differs from the source (§3.5.1, §3.5.2, §3.5.4, §3.5.6, §3.5.8) |
| G5 | a sum variable is neither an action argument, a field of a constructor in one, nor equality-fixed (§3.5.5) |
| G6 | the interface declaration does not match the model (§3.6), or the project table is wrong (§3.6.9) |
| G7 | a catalogue definition in the model differs from the catalogue's text (§3.3.6) |
| G8 | an operation outside its allowed cost class or unsupported by its representation, an intermediate that could leave `i128`, or two in-place updates that read each other (§3.4.8, §3.10.4, §3.12.4, §3.13.1) |
| G9 | the policy is incomplete or names what is not there; a selector for a variable that is not free; an index whose predicate, class, key or class condition is not local or reads what §3.7.6 forbids; a `defer` of a summand without a free variable; a `maps` name without `p_`; a model or policy name starting with `ix_` (§3.7.1, §3.7.4, §3.7.6) |
| G10 | a construction assertion failed (§3.9.1): a defect of the generator |
| G11 | the per-id layout exceeds the project's limit (§3.13.4) |
| G12 | a sort outside §3.3, or an action argument of a sort outside §3.5.7 |
| G13 | a `newest` or `oldest` index (not a `within` one) reads a parameter that some summand writes other than at ids visible in its assignments (§3.7.4 item 3) |

Not implemented yet: G8 other than the one on two in-place updates (§3.4.8), and G11.

#### 3.14.2 Errors of the generated code

| error | raised by | when |
|---|---|---|
| `Refused(event)` | stepper | no input summand accepts the event (§3.8.2); the state is unchanged; the caller reports it as a protocol error |
| `Overflow(summand, target)` | stepper, acceptor | a value does not fit its width (§3.12.4) |
| `Undefined(summand, operation)` | stepper, acceptor | head or top of an empty collection, `Int2Nat` of a negative, `Nat2Pos` of 0, a second write to a fixed per-id list |
| `StepLimit` | stepper | more than `2^24` actions in one step (§3.8.3) |
| `NotQuiescent` | stepper, debug builds | an event arrives in a state that is not quiescent (§3.8.2) |
| `Rejected(entry)`, `Ambiguous(entry)` | acceptor | §3.8.9 |
| `DecodeError` | `decode` | a malformed entry |

Each is a value of the generated error type (Rust) or an instance of the generated
exception class (Python), never a panic, an abort or a generic exception, so the
caller can end its run with it as the cause.

### 3.15 Regeneration at every commit

#### 3.15.1 The same inputs give the same bytes

The generated files are a function of the inputs of §3.1.1, the project table, the
generator's own source and the toolset version, for every input the generator
accepts. `mcrl22lps` is not deterministic for a Core that is not linear, whose program
counter and dummy globals vary between runs; such a Core is refused (G4), so this
never reaches the output. Every linear Core tried linearised byte-identical in
repeated runs on 2026-10-07. A constant listed under `[constants]` is emitted as a
named constant of each backend even where the rewriter has inlined its value into the
summands; the inlined occurrences are literals, and the check of §3.4.3 is made on the
LPS's data specification, which keeps every map and equation. No time stamp, absolute
path, host name or user name appears; every collection is emitted in a fixed order;
names are those of §3.5.3; line endings are LF. The generator formats its output
itself. No formatter is run on it, and a project's formatters and linters should
exclude `<generated>` from automatic fixing.

#### 3.15.2 The manifest

`<generated>/MANIFEST.json` records the SHA-256 of the generator's source files, the
toolset version, the SHA-256 of every input file, the summand table (name, input or
output, policy position, cost class), the walks, the per-id layout of each backend,
the stepper's indexes and deferred summands (or null without a policy), the systems
written under `<generated>/model/`, and the SHA-256 of every generated file.

Not implemented yet: the cost class of a summand and the layout, both null (§3.13.1,
§3.13.4).

#### 3.15.3 The diff check

`naima-codegen generate --check` regenerates into a temporary directory and compares
the result byte for byte with `<generated>`. A file added, missing or different fails
the check, which prints the first differing file and line. A project runs it as a
pre-commit hook on every commit whose changes touch the model directory, the
catalogue, the generator or the generated directory, and in its gates; installing the
hook is the project's. A commit that changes an input without regenerating, or edits
a generated file, is then refused.

#### 3.15.4 No silent skip

When the pinned toolset is not installed, the check fails and says so; it never
passes by skipping.

#### 3.15.5 Regeneration invalidates the checks

A change under `<generated>/model/` changes the digest of the files the property items
of §3.9 read, so a tracker that records verification evidence by digest, such as
Naima, reports them no longer current until they are verified again.

### 3.16 Comparing the backends

3.16.1. Same actions. A replay harness feeds each event sequence to every backend the
tests build and compares, after every event, the actions returned, the error raised if any, and every
observable. The sequences are recorded runs of the project, random walks of the
refined systems' state spaces on the reference instances projected to events, and
seeded random event sequences, legal or not, which must give the same actions or the
same error at the same event.

Not implemented yet: recorded runs (the project's to supply) and observables. The
tests compare actions, errors and the encoding of the whole state after every event
on random walks and seeded random sequences, in Rust, Python and TypeScript at once,
on every example.

3.16.2. Replay reproduces the run. Feeding the events of a run's record to any
backend gives exactly the actions of that record.

3.16.3. Speed side by side. The CPU measurements of §3.13.2 of each backend, on the
same machine and sequence, are reported together.

Not implemented yet, with §3.13.2.

3.16.4. Identities. Left to the project. The generated code computes no identity of
anything (§3.8.8); identities the caller computes are the caller's.

3.16.5. The comparison harness. A Python program run on free-threaded CPython imports
the Python backend and the binding (§3.11.4), builds both `State`s from one `Config`, and for
each event of a sequence calls `step` on both with the same tuple. After every event
it requires the two action lists to be equal as tuples, the `encode` bytes of every
action to be equal between the two modules, the same error class with the same
arguments or no error on both, and every observable to be equal. `bytes()` is not
compared, since the layouts differ by design (§3.13.4). Since the binding's own
conversion code could hide or create a difference, a Rust test feeds the same
sequences to the crate natively and compares the `encode` bytes of every entry with
those the binding produced, so the binding is shown transparent and what the harness
compares is the crate a caller links.

Not implemented yet: the binding and the harness built on it. The tests compile the
crate with a native driver that reads an event sequence and prints, after every event,
the actions, the error and the state's encoding, run the TypeScript module the same way
under Deno (§3.18.5), and require both outputs to equal the Python backend's line for
line.

3.16.6. Records of real runs through the backends. Left to the project: replaying the
records of its own runs through the backends it chose, with the configuration each run
was built from, and requiring byte-identical action frames and acceptance of every
entry.

3.16.7. A difference. The harness stops at the first event where the two differ and
reports the event's sequence number, the event, both action lists or errors and the
observables that differ, and writes the sequence prefix as a regression case. A
difference is a defect of the generator, of a catalogue implementation or of the
binding, and fails the commit's check. It is never settled by preferring one backend,
since none is the reference of another:
the prefix is replayed in the refined Core with the toolset's simulator (`lpssim` on
the refined LPS, §3.7.4), whose actions decide which backend is wrong.

Not implemented yet: writing the prefix as a regression case and the replay in
`lpssim`.

3.16.8. Each backend against mCRL2. Comparing backends finds where they differ, not
where they agree on something wrong, so each is also checked against mCRL2 itself:

1. Its maps against the rewriter. Every user map, walks included, on seeded random
   arguments, gives the value mCRL2's rewriter gives (`lps2lts` on a process whose
   action labels print the values). In Rust, Python and TypeScript.
2. Its stepper's state space against the refined Core's. On each example, with the id
   sorts bounded as in §3.17 and a configuration given to the environment's
   `CoreStart`, the refined Core alone, every input offered in every state where it is
   enabled, is explored by `lps2lts`. The stepper's own state space is explored the
   same way: from the state `s0` of §3.8.1, the first enabled output in policy order if
   there is one, otherwise every event over the bounded domains. The two must be
   strongly bisimilar (`ltscompare -ebisim`); §3.18.5 says why strong bisimulation is
   the right equivalence. In Python and TypeScript; Rust is held to the same state
   space through the comparison of §3.16.1.

Not implemented yet: the state-space comparison of the Rust stepper itself.

### 3.17 Reference instances

The checks of §3.9 and the model's own properties run at least on the instances of
the model directory, one file each under `<model>/instances/`. Which instances exist,
and why each does, is the project's; removing one should need a reason written down.

The generator assembles the instances into groups and writes one system of each kind
per group (§3.9.7): in a group, the id sorts are bounded to the group's universe of
ids and the instances' values are renamed apart, so that one exploration covers every
instance of the group. A property item names the group it is about through its
`model` (§3.6.9).

The groups are declared in `codegen.toml`:

```toml
[systems.groups]
small = ["a", "b"]        # one system for the instances a and b
c = ["c"]

[systems.ids.Job]         # one table per id sort that needs other than the defaults
prefix = "J"              # the constants J0, J1, ... (default: the sort's first letter)
header = "jobs"           # an instance says `% @jobs 3` (default: the sort's name, lower case, with an s)
bound = 2                 # the size when an instance of the group has no header
```

The groups come in the order of `[systems.groups]`, each with its instances by name,
then one group of its own, named after it, for every instance that no group lists.
An instance may be in more than one group. A group that lists an unknown instance,
an empty group, or a group name that is also the name of an instance in no group is
G6. In a group's systems every id sort of `id_sorts`, declared in Core as
`sort S = struct c(p: Nat);`, becomes `sort S = struct P0 | ... | Pk;` with `c` and `p`
kept as maps, where the size is the largest `% @<header> n` of the group's instances
(or `bound` when one of them has none; neither is G6), and every `c(i)` in the
instances' text becomes `Pi`.

The text conventions the systems rely on: in an instance file, `@I` stands for its
constant `i_<name>` of the sort `Inst`; Core holds the placeholder comment
`%@observers` where the observers' summands go; the environment `env.mcrl2` has a
`proc CoreStart` that calls `Core(`, the placeholder `%@obs-allow` in its allow set,
and a `comm({ ` list; `observers.mcrl2` declares its actions after the line
`% @act`, its summands after `% @summands`, and anything after `% @witness` is left
out of the systems.

The number of states of the model on each instance, and the time to verify every
property including the checks of §3.9, are measurements a project may hold to a
budget.

### 3.18 The TypeScript backend

The TypeScript backend is for a user interface whose logic is the model: components
hold no logic of their own, read the state and invoke transitions, and every side
effect leaves as an action and comes back as an event. Like the other backends, it is
generated from the refined Core, checked by the diff check (§3.15), compared with the
other backends (§3.16) and checked against mCRL2 itself (§3.18.5).

#### 3.18.1 The modules

`<generated>/typescript/<typescript_module>/` holds two modules:

- `<typescript_module>.ts`, plain TypeScript. It imports nothing, uses no Svelte, no
  timer, no clock, no random number and no I/O, and holds everything the model
  decides: the types, the configuration, the acceptor, the entry codec, and with a
  policy the stepper and its transition table (§3.18.3).
- `<typescript_module>.svelte.ts`, written only when the model has a policy. It imports
  only the plain module, and wraps its stepper in a Svelte 5 class (§3.18.4).

Without a policy there is no stepper, so only the plain module is written (§3.1.1).
Both modules are ES modules that import with the `.ts` extension, as Deno and
TypeScript's `allowImportingTsExtensions` read them; a bundler such as Vite reads
them as they are.

#### 3.18.2 Values

| sort | TypeScript |
|---|---|
| `Bool` | `boolean` |
| `Nat`, `Pos`, `Int`, an id sort | `number`; an id sort is a type alias of `number` named after the sort |
| enumeration | the name of its constructor, a string, typed as the union of the names; `<Sort>_VALUES` lists them in declaration order |
| record | the array of its fields, `readonly [T1, T2]` |
| union | the array of its constructor's index and fields, `readonly [0, T1] \| readonly [1]` |
| `List(X)` | `readonly X[]` |
| `FSet(X)` | `readonly X[]`, kept in the model's order without repeats |
| `K -> X` | `Fn<K, X>`, read with `get(k)`; a default and a `Map` of the entries that differ from it |

The model's order is the one mCRL2 gives the sorts: numbers by value, `false` before
`true`, constructors in declaration order, arrays element by element. Every integer
result is checked to be a safe integer (at most 2^53 - 1 in magnitude, where a double
holds integers exactly) and is `Overflow` otherwise; widths are checked at every write
as in §3.12.4. An index keeps, per member, its class and order key, and `first` scans
the members; after every summand each index is refreshed at every id below its bound
and at every member, so no write needs to record dirty ids. These are the backend's
representations, chosen for state the size of a user interface's; their costs are not
those of §3.10.3 and §3.13 does not hold the backend to them.

A state is never changed once a step has returned it: `step` copies it first (a
shallow copy, the indexes copied), and a summand copies a stored function before it
writes it. A component that keeps a state, or one of its stored functions, keeps a
value that does not change under it, and Svelte sees a new value exactly where a
parameter changed.

#### 3.18.3 The plain module

```ts
export type <Sort> = ...;                       // one per sort of the model (§3.18.2)
export interface Config { readonly <p>: T; }    // one field per configuration parameter
export interface State { readonly <p>: T; }     // every parameter of Core, read only
export type Event = { readonly type: "<name>"; readonly <arg>: T } | ...;   // one per input label
export type Action = { readonly type: "<name>"; readonly <arg>: T } | ...;  // one per output label
export type Entry = { readonly event: Event } | { readonly action: Action };

export function start(config: Config): State;               // s0 of §3.8.1, before the output loop
export function initial(config: Config): Step;              // the constructor: s0 and its output loop
export function step(state: State, event: Event): Step | null;  // §3.8.2; null: refused, nothing changed
export function allowed(state: State, event: Event): boolean;
export function quiescent(state: State): boolean;
export interface Step { readonly state: State; readonly actions: readonly Action[]; }
export const inputs: readonly InputTransition[];            // the transition table
export const outputs: readonly OutputTransition[];
export class Acceptor { constructor(config: Config); accept(entry: Entry): void; }
export function encode(entry: Entry): Uint8Array;
export function decode(data: Uint8Array): Entry;
export function stateBytes(state: State): Uint8Array;
export function stateKey(state: State): string;
export function mcrl2Label(entry: Entry): string;
export class StepError extends Error {}   // Undefined, Overflow, StepLimit, NotQuiescent
export class Reject extends Error {}      // Rejected, Ambiguous
export class DecodeError extends Error {}
```

An event's and an action's `type` is its label without `c_`, in camel case (`c_give_up`
is `giveUp`); its fields are the label's `args` (§3.6.3). `step` returns `null` for an
event no input summand accepts (§3.8.2) and throws a `StepError` for any other error,
which ends the run (§3.8.5). `encode` writes the bytes of §3.8.10, equal in every
backend; `stateBytes` the encoding of every parameter, equal in every backend for equal
states (§3.16.5).

The transition table is the refined Core's summands as data: `inputs` has one entry per
input summand, in the model's order, and `outputs` one per output summand, in policy
order. Each entry has the summand's name, its label, its `type`, its arguments with
their sorts (`bool`, `int`, `id`, `enum` with its values, or `other`), and `next`: for
an input, the state after the summand fires on an event, or `null` when it does not
accept it; for an output, the action and the state after it, or `null` when it is not
enabled. `step`, the output loop and the table read the same lists of summands, so
whatever explores the table explores what `step` does. From `start(config)`, the first
enabled output of `outputs` is a step inside the output loop, and in a state where none
is enabled (`quiescent`) every entry of `inputs` may take an event. `stateKey` is equal
for two states exactly when every step from them is the same: the parameters and the
order of the `newest` and `oldest` indexes. `mcrl2Label` writes an entry as mCRL2
writes the action, with ids in the model's form (`job(3)`). These are what §3.18.5
explores.

#### 3.18.4 The Svelte module

```ts
export interface <Name>Port {                   // <Name>: typescript_module in camel case
  <output>(<args>): void;                        // one method per output label
  refused?(event: Event): void;
}
export interface <Name>View { readonly <p>: T; }  // every parameter, read only
export class <Name> {
  constructor(config: Config, port: <Name>Port);
  readonly view: <Name>View;
  get stopped(): StepError | null;
  allowed(event: Event): boolean;
  <input>(<args>): boolean;                     // one method per input label
}
```

The class keeps each parameter in a `$state.raw` field of its own behind `view`, so a
component that reads a parameter is updated when that parameter changes, and only
then. It holds no logic: an input method builds the event, calls `step` of the plain
module, and either changes nothing and returns `false` (the model does not allow the
event now; the port's `refused` is told), or takes the new state, sets the fields that
changed, calls the port once per action, in order, and returns `true`. The port's
methods are where side effects happen; their results come back as calls of input
methods. A port that calls an input method while it is being called is served after
the actions before it, so the port sees the actions in the order the model takes them.
A `StepError` stops the machine: `stopped` holds it and every later input returns
`false`. `allowed` is what a component enables a control with.

#### 3.18.5 Checked against mCRL2

The tests run the modules under Deno 2 and check:

1. Types. `deno check` of both modules, with the declarations of Svelte's runes from
   `npm:svelte@5.57.2` in scope.
2. The Svelte module. Its types are stripped with the transpiler of
   `npm:typescript@5.9.3`, the result is compiled by `compileModule` of
   `npm:svelte@5.57.2` (a warning fails), and the compiled module is run headless next
   to the plain module on seeded random event sequences: after every event the
   method's result says whether `step` accepted it, the port received the step's
   actions in order, the view holds the step's state, and an effect that reads the view
   ran again exactly when the state changed.
3. The maps against mCRL2's rewriter, and the stepper's state space against the
   refined Core's (§3.16.8), on every example.
4. The acceptor accepts every entry of seeded random paths of the model systems
   (§3.9.7), with the bytes the other backends write (§3.8.10).
5. The comparison with Rust and Python after every event (§3.16.1).

The state spaces are compared under strong bisimulation, not branching bisimulation.
Branching bisimulation abstracts from internal steps, and neither side has one: in
the refined Core every summand is a labelled action, and the exploration of §3.16.8
records every summand the code fires, inputs and outputs alike, as a labelled
transition, so the actions inside one step are compared one by one and in order. On
two systems without internal steps the two equivalences coincide; the comparison
asks for strong bisimulation so that it states what it checks, and so that a label
lost on either side, which branching bisimulation would read as an internal step,
fails instead of passing.

## 4. Invariants

Each is a candidate property of the model (P), a test (T), a static check of the
generator (S) or a metric (M).

1. Refinement. On every reference instance, the traces of the system with the refined
   Core are traces of the system with Core (P, §3.9.2).
2. Non-blocking. No reachable state of the refined system has an output allowed by
   the model and none by the refined Core (P, §3.9.3).
3. Properties survive the policy. Every property of the model holds on the refined
   system (P, §3.9.4).
4. Every step ends. No reachable state of the refined system starts an infinite path
   of outputs alone (P, §3.9.5); at run time no step reaches `2^24` actions (T).
5. One summand per label. Summands that share a label are never enabled together with
   the same arguments (P, §3.9.6).
6. Receptive. In a quiescent state, Core accepts every event the environment can send
   (P, §3.9.7).
7. Quiescent between steps. Every state returned by `new` or `step` is quiescent (T in
   debug builds, §3.8.2; by construction, §3.8.3).
8. A refused event changes nothing. After `Refused`, the state equals the state before
   the event (T, §3.8.2).
9. Determinism. The same configuration and event sequence give the same actions,
   errors and observables in every run (T, §3.8.6); a Rust build with a different hash
   function gives the same actions (T, §3.10.5).
10. Backends agree. On every sequence of §3.16.1 the backends give the same
    actions, errors and observables after every event (T, §3.16.1).
11. Replay reproduces the run. The events of a record give back its actions (T,
    §3.16.2).
12. The stepper stays inside the model. Every record the stepper produced is accepted
    to its end by the acceptor of every backend (T, and on every run that has a
    monitor, §3.9.8).
13. Generated means generated. Regenerating gives `<generated>` byte for byte (S,
    §3.15.3).
14. Sans-I/O. The generated code and the catalogue import no I/O, thread, clock or
    random module; the plain TypeScript module imports nothing and the Svelte module
    only the plain one (T, §3.11.1, §3.12.1, §3.18.1).
15. No panic. The Rust code has no panicking path (S, §3.11.3).
16. Catalogue conformance. Each implementation equals its mCRL2 reference on the
    random operation sequences (T, §3.10.1).
17. Configuration is constant. No summand assigns a configuration parameter (S,
    §3.6.2).
18. Bounded step. No summand contains an operation whose cost grows with the number of
    ids (S, §3.13.1); the time per event does not grow with the number of ids (M, the
    project's, §3.13.3).
19. Bounded memory. The columns cost at most the project's limit per id (S, §3.13.4),
    and so does the whole state per id on a large run (M, the project's).
20. CPU per unit of work within the project's budget in each backend (M, the
    project's, §3.13.2).
21. Records of real runs replay through every backend the project chose (T, the
    project's, §3.16.6).
22. The binding is transparent. The crate called natively and through the binding
    give byte-identical `encode` output on every comparison sequence (T, §3.16.5).
23. A caller that runs only the Rust crate never loads the Python package or the
    binding (T, the project's).
24. Head-enabled or deferred. In no reachable state of the refined system is the
    refined Core quiescent while the model enables an output summand the policy does
    not defer (P, §3.9.3); every deferred summand is listed with its reason (S,
    §3.7.1).
25. Indexes are their definitions. After every summand, each index of the code has
    exactly the members, order and classes its definition gives, and no member at or
    above its bound (T, §3.9.9); the refined Core's choices are those of the reference
    definitions (by construction, §3.7.4).
26. The stepper is the refined Core. The state space of the Python and TypeScript
    steppers on each example is strongly bisimilar to the refined Core's (T, §3.16.8,
    §3.18.5).

## 5. Requirements covered

Left to the project. A project lists, in its own tracker, which of its requirements
this specification serves and where.

## 6. Open points

1. The catalogue (§3.10): the entries with `ops.toml`, their implementations and
   conformance tests, the representations of §3.10.3 and their choice (§3.10.4).
2. Cost and memory (§3.13): cost classes, G8 beyond §3.4.8, the per-id layout, a
   per-id limit declared by the project and G11; the range analysis of §3.12.4.
3. Observables (§3.6.7) in every backend.
4. The Python binding of the Rust crate (§3.11.4) and the comparison harness built on
   it (§3.16.5); regression cases and the `lpssim` replay of a difference (§3.16.7).
5. A grouping of instances, and a bounding of id sorts, that do not depend on one
   model (§3.17).
6. Where a project declares its budgets (§3.13.2, §3.13.3, §3.13.5) so that the
   generator or its tests can read them.
7. The state-space comparison of §3.16.8 for the Rust stepper.
8. In TypeScript, integers beyond 2^53 - 1 (§3.18.2): `bigint` for the widths `u64` and
   `i64`, or the range analysis of §3.12.4 refusing what could leave the safe range.
