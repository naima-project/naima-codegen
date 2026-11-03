# Changelog

## Unreleased

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
