# Changelog

## Unreleased

### Portable: it runs through uv, with no Python on the machine

`naima-codegen` and `naima-codegen.cmd` run the tool with `uv run --project`, which downloads the Python that
`pyproject.toml` pins (3.13) on first use, with no administrator rights; with no uv on the machine uv's official installer is
run for the user. `NAIMA_PYTHON` still names a Python to run it with instead. `naima-codegen --version` prints the version and
the commit of the checkout. The generator's own files changed, so a project's manifest is regenerated once.

### `generate --check` is neutral to line endings

The manifest hashed the model's files and the generator's own sources as they lie on disk, so a checkout that converts line
endings (Windows, `core.autocrlf`) holds them with CRLF and `generate --check` reported the manifest as differing (the
hash of `naima-codegen` or of an input). It now hashes the text with LF, whatever the checkout holds; the generated files
were already written with LF and read back with universal newlines. Files with LF hash as before. The hash of
`naima_codegen/outputs.py` changed with this fix, so a project's manifest is regenerated once (`naima-codegen generate`).

### Trace inclusion ignores the observers' steps

`check-refinement` compared the refined system with the model by the strong `trace-ac` preorder, under which the
hidden observer steps still count: a model that breaks an invariant, so that an observer fires, also failed trace
inclusion and the refinement was blamed. It now uses `weak-trace-ac` (docs/spec.md §3.9.2). A fired observer is
reported only by the separate "observers fired" line.

### A dropped instance's columns free their storage

The generated Rust kept a function's entries at id keys in a vector grown to the largest id ever written, and a growing
list's per-id slots likewise, so a long run held about a kilobyte of columns per instance it ever made, live or not
(VoxLogicA-2's frame model on a flat loop). Both now keep their id entries in pages of 256 ids, each freed once all its
entries are back to the default (docs/spec.md §3.11.5): ids are still never reused, their storage is. Reading and
writing an id entry stays an index; values read are unchanged.

### A selected variable is bound once per expression

The refined Core used to copy a selector's term (`first(...)`, `if(some(a), first(a), ...)`) at every occurrence of
its variable in the summand's condition, action and assignments, and into the enabledness maps built from them: a
selector of several cases used often made the refined, probe and stepper code grow with the product of the two.
VoxLogicA-2's frame model wrote one four-case admission choice 971 times into its refined Core; compiling the rewriter
of a 415-state probe system took 229 s instead of 133 s. Each expression that reads a selected variable is now
bound once with `whr` (docs/spec.md §3.7.2), in mCRL2 and in the Rust, Python and TypeScript steppers (a `let`), and a
selector is still evaluated only after its `when` holds. The behaviour of the generated code is unchanged.

Regenerate a project (`naima-codegen generate`) to pick this up; `generate --check` refuses a generated directory written
by the earlier generator.

### TypeScript a strict project and Node import as it is

The generated TypeScript declared some classes with parameter properties (`constructor(readonly b: V)`), which are not
erasable syntax: Node, which runs TypeScript by type stripping, refused the module, and a project compiling with
`erasableSyntaxOnly` or `noUncheckedIndexedAccess` got errors in it. Both modules now use erasable syntax only and
type-check under the strictest options (docs/spec.md §3.18.1); the tests check them under those options and import the
plain module with Node. What the modules do is unchanged.

A project whose only backend is TypeScript (or Python) no longer fails to generate: the cost check of §3.13.1 reads Rust
step code emitted for it alone when Rust is not a chosen backend.

Regenerate a project (`naima-codegen generate`) to pick this up; `generate --check` refuses a generated directory written
by the earlier generator, since the files and the generator's hashes in `MANIFEST.json` both change.

### A Svelte machine's port is now its outputs

The interface through which a generated Svelte machine carries out its output actions was called its port. It is now
called its outputs, matching the model's input and output labels: the input methods are the inputs, the object passed to
the constructor is the outputs.

- `<module>.svelte.ts` exports `<Name>Outputs` instead of `<Name>Port`, and the constructor is
  `constructor(config: Config, outputs: <Name>Outputs)`. The methods are unchanged: one per output label, and
  `refused?(event)`.
- The generated comments say outputs where they said port (docs/spec.md §3.18.4).

Deprecated, for one release, then removed: every generated Svelte module still exports `<Name>Port`, marked
`@deprecated`, as an alias of `<Name>Outputs`, so code that names the old type keeps compiling.

Regenerate a project (`naima-codegen generate`) to pick this up; `generate --check` refuses a generated directory written
by the earlier generator, since the files and the generator's hashes in `MANIFEST.json` both change.
