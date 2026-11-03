# Changelog

## Unreleased

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
