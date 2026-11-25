# Example: the form

[examples/form](../../examples/form) is the state machine behind a form in a user interface. The model is small on
purpose, and it is the example that generates the TypeScript backend with its Svelte class.

## The model

The user edits the form, and each edit says whether what is typed is valid. A valid form can be submitted while attempts
are left. Submitting queues a send. Sending is the form's own decision, the output `c_send`, which carries the number of the
attempt. The answer comes back as an input, `c_succeeded` or `c_failed`. A failure lets the user edit again or resubmit
until the limit of attempts is used up.

| label | kind | meaning |
| --- | --- | --- |
| `c_edit(valid)` | input | The user changed the text, and it is or is not valid. |
| `c_submit` | input | The user pressed the button. |
| `c_succeeded`, `c_failed` | input | The answer to a send. |
| `c_send(attempt)` | output | The form decides to send the draft. The user interface does it and reports the answer. |

The process has five parameters. `limit` is configuration, the maximum number of attempts. `phase` is one of `EDITING`,
`SENDING`, `SENT` and `FAILED`, and `valid`, `tries` and `queued` complete the state. The policy has one output, so
`policy.toml` is a single line.

## What is checked

There are two instances, `once` (three attempts, the server fails the first send) and `never` (two attempts, the server
fails every send), in one group. `check-refinement` shows that the refined process behaves as the model and that
every step ends. The property `one-send-per-submit.mcf` says that no send happens before the first submit and that two
sends are never made without a submit between them. It holds.

## Using it from a Svelte component

The generated `form.svelte.ts` holds the state as Svelte 5 `$state` fields behind a read-only `view`, with one method per
input. The output actions are calls to an object the component passes in. The component contains no logic about the
form: whether the button is enabled, what a click does and what is shown all come from the model.

```svelte
<script lang="ts">
  import { Form } from "./generated/typescript/form/form.svelte.ts";

  const form = new Form({ limit: 3 }, {
    send: (attempt) => api.post(draft, attempt).then(() => form.succeeded(), () => form.failed()),
  });
  let draft = $state("");
</script>

<input bind:value={draft} oninput={() => form.edit(draft.trim() !== "")} />
<button disabled={!form.allowed({ type: "submit" })} onclick={() => form.submit()}>Send</button>
{#if form.view.phase === "SENT"}<p>Sent.</p>{/if}
```

`form.ts`, next to it, is plain TypeScript with no imports. `step(state, event)` returns the next state and the actions, or
`null` when the model does not allow the event.
