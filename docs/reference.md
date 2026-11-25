# Reference: codegen.toml and policy.toml

A project has two files that configure the generator. `codegen.toml` declares the interface of the model's process and
says what to generate. `policy.toml` says which output the generated code takes when the model enables several, and it is
optional. Both are read by `naima_codegen/config.py` and `naima_codegen/policy.py`; `tests/test_docs.py` checks that
every key listed here exists in the code and that every key the code accepts is listed here. The sections of
[spec.md](spec.md) that define each key are given in brackets.

A key the generator does not know is an error (G6 for `codegen.toml`, G9 for `policy.toml`), so a misspelled key never
passes silently.

## codegen.toml

`codegen.toml` sits at the project root, or at `model/codegen.toml` when the model directory is `model`. Having both is
an error. The `generated/` directory always ends up with the same layout, written by `naima-codegen generate`.

### Top level (spec §3.6)

| key | type | meaning |
| --- | --- | --- |
| `mcrl2_version` | string, required | The mCRL2 release the model is written for, for example `"202607.0"`. The generator refuses to run with another release on `PATH`. |
| `id_sorts` | list of strings | The names of the id sorts: sorts like `sort Job = struct job(jx: Nat)` that stand for numbered things, which columns are indexed by. |
| `constants` | table | Constant maps the model may use, as `name = "Sort"`. |
| `widths` | table | Default integer width per sort, for example `Nat = "u32"`. Without it `Nat` and `Pos` are `u64`, `Int` is `i64` and an id sort is `u32`. |
| `cost_known` | list of strings | Sites of the form `"<summand>: <kind> <parameter>"` whose cost the generator should accept although it cannot bound it (§3.13.1). |
| `bytes_per_id` | table | Per id sort, the limit in bytes of the generated per-id layout (§3.13.4). |
| `key_sorted` | table | Per list column, the key function the list is kept sorted by (§3.10.7). |
| `parameter` | array of tables | One `[[parameter]]` per parameter of `proc Core`, in order. |
| `action` | array of tables | One `[[action]]` per label of the model. |
| `observable` | array of tables | Read-only getters over the state. |
| `project` | table | Where things are and what to generate. |
| `systems` | table | How the instances are grouped for the checks. |

The values of `widths` are `u8`, `u16`, `u32`, `u64`, `i32` and `i64`.

### `[[parameter]]` (spec §3.6.2)

| key | type | meaning |
| --- | --- | --- |
| `name` | string, required | The name of the parameter in `proc Core`. |
| `role` | string, required | One of `config` (never assigned, becomes a field of the configuration given to the constructor), `column` (a function over an id sort), `table` (a function over an enumeration), `map` (a function over any other sort), `frontier` (a collection bounded by the active ids), `scalar` (anything else). |
| `sort` | string | The sort as mCRL2 text. Optional; when given it is checked against the linearised process. |
| `init` | string | An mCRL2 expression over the `config` parameters that gives the initial value. Without it the initial value is the one in `model/codegen-init.mcrl2`. |
| `repr` | string | The representation, when the sort admits several: `bitset`, `adj-fixed`, `adj-append` or `deque`. |
| `width` | string | The integer width, one of the values listed under `widths`. |

### `[[action]]` (spec §3.6.3)

| key | type | meaning |
| --- | --- | --- |
| `label` | string, required | The action's label as written in the model, for example `c_start`. |
| `kind` | string, required | `input` for an event the environment sends, `output` for an action the process decides. |
| `args` | list of strings, required | The names of the action's arguments in the generated types. The list is empty for an action with no argument. |

Every label of the model is listed once, with one exception: a label may be an input and an output with different numbers
of arguments, which gives two entries.

### `[[observable]]` (spec §3.6.7)

| key | type | meaning |
| --- | --- | --- |
| `name` | string, required | The name of the getter. |
| `expr` | string, required | An mCRL2 expression over the parameters. |
| `arg` | string | The name of an id argument. Given together with `sort`. |
| `sort` | string | The id sort of that argument. |

The spec marks observables as not implemented yet in the generated code; the declaration is read and checked.

### `[project]` (spec §3.6.9)

| key | type | default | meaning |
| --- | --- | --- | --- |
| `backends` | list of strings | `["rust", "python"]` | The languages to generate: `rust`, `python`, `typescript`. |
| `rust_crate` | string | none, required with `rust` | The directory of the generated crate under `generated/rust/`. |
| `rust_package` | string | the value of `rust_crate` | The Cargo package name. |
| `rust_workspace` | boolean | `false` | With `true` the generated `Cargo.toml` inherits version, edition, lints and so on from the enclosing Cargo workspace. |
| `rust_loop` | string | none | The name of a second crate, written under `generated/rust/`, with the `Handlers` trait and the generic event loop. Needs `rust` and a policy. See [event-loop.md](event-loop.md). |
| `python_package` | string | none, required with `python` | The package is `generated/python/<name>/__init__.py`. |
| `typescript_module` | string | none, required with `typescript` | The modules are `generated/typescript/<name>/<name>.ts` and `<name>.svelte.ts`. |
| `model` | path | `"model"` | The model directory. |
| `generated` | path | `"generated"` | The output directory. |
| `catalogue` | path | `"catalogue"` | The directory of data-structure implementations the generator may use. |
| `properties` | path | none | A directory of property items: each is `<item>/meta.json` naming the system and the `.mcf` formula. |

Paths are relative to the project and may not leave it.

### `[systems]` (spec §3.17)

The checks run on instances, one file each under `model/instances/`. A group puts several instances in one state space.

| key | type | meaning |
| --- | --- | --- |
| `groups` | table | `group = ["instance", ...]`, in the order the groups are checked. |
| `ids` | table of tables | `[systems.ids.<Sort>]`, one per id sort that needs more than the defaults. |

### `[systems.ids.<Sort>]`

| key | type | meaning |
| --- | --- | --- |
| `prefix` | string | The constant names of the sort's ids, `J` giving `J0`, `J1` and so on. Default the sort's first letter. |
| `header` | string | The word an instance uses to say how many ids it has, as `% @jobs 3`. Default the sort's name in lower case with an `s`. |
| `bound` | positive integer | The number of ids in a group whose instances do not say. |

## policy.toml

The model says what may happen. The policy says which of the enabled outputs the code takes. Terms are mCRL2 text over
the parameters, so the policy is part of what the model checker sees: the refined process is the model with the policy
applied, and `check-refinement` shows that it stays inside the model.

### Top level (spec §3.7.1)

| key | type | meaning |
| --- | --- | --- |
| `order` | list of strings | Every output summand's name, highest priority first. It is total: a missing name, a repeated name or a name that is not an output is an error. |
| `select` | table | `[select."<summand>"]`, the choice of a value for each free sum variable of an output summand. |
| `index` | table | `[index.<name>]`, the ordered sets the selectors can read. |
| `maps` | string | mCRL2 text declaring maps that selector terms use. Their names start with `p_`. |
| `defer` | table | `"<summand>" = "<reason>"` for an output summand the policy deliberately does not serve. |

A selector is written `variable = { term = "...", when = "..." }`. `term` is the chosen value and `when` the condition
under which it is defined. Selector terms may use `least(W)`, `first(J)` and `some(J)`, where `W` is a set parameter and
`J` an index, besides the maps and constructors of the model.

### `[index.<name>]` (spec §3.7.6)

| key | type | meaning |
| --- | --- | --- |
| `sort` | string, required | The id sort of the members. |
| `var` | string, required | The name of the member variable in the other terms. |
| `bound` | string, required | A term over the parameters that bounds the ids that can be members. |
| `member` | string, required | The membership predicate. It reads only columns at the member itself, so a change at one id never needs a rescan. |
| `order` | string, required | `least`, `key`, `newest`, `oldest` or `within`. |
| `key` | string | The ordering term, given exactly when `order` is `key`. |
| `key_sort` | string | `Nat` or `Int`, when the key is not a plain column read. |
| `within` | string | The name of another index whose order this one takes, given exactly when `order` is `within`. |
| `class` | string | A term of an enumeration sort that classifies the members. Given together with `admits`. |
| `admits` | table | `{ var = "k", when = "..." }`, the condition on a class that makes its members eligible for `first`. |
