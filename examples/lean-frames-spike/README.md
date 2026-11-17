# The frame machine's cache bound, proved in Lean 4

`gen/` is what `naima-codegen lean` writes for VoxLogicA-2's frame machine (its
`model/frames`): `Frames.lean`, the refined Core with its observers as a Lean 4
transition system, and `Mcrl2.lean`, the part of mCRL2's library it uses. The
property `cache-bound`, `[true* . o_cache] false`, is generated as the
proposition `Frames.prop_cache_bound`. `CacheBound.lean` proves it, written by
hand, for every configuration (w, r, cache, the slow-start limit), every
program the inputs give, and every interpretation of the maps that satisfies
the model's equations.

Regenerate, from the root of naima-codegen, with the frame model at `<frames>`:

```sh
python3 naima-codegen lean --out examples/lean-frames-spike/gen --namespace Frames --free limit --project <frames>
```

Check, with Lean 4.36.0 (`naima tools install lean`; `naima tools path lean lake`):

```sh
lake build
```

The build ends by printing the axioms the theorem depends on: `propext`,
`Classical.choice` and `Quot.sound`, Lean's standard ones.
