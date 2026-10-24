// Event sequences through a generated TypeScript stepper (docs/spec.md §3.16.1, §3.18.5).
//
//   deno run --allow-read replay.ts <module.ts> <sequences.json>
//
// sequences: [{"config": {...}, "events": [Event...]}]. Prints, per sequence, "init <actions> <state>", then per
// event "ok <actions> <state>" or "err <class>" (a refusal is "err Refused"; any other error ends the sequence),
// then "-"; actions are the hex of their entries, comma separated, the state the hex of stateBytes.
const [modPath, seqPath] = Deno.args;
const M = await import("file://" + modPath);
const seqs = JSON.parse(await Deno.readTextFile(seqPath));
const hex = (b: Uint8Array) => [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
const acts = (a: unknown[]) => a.map((x) => hex(M.encode({ action: x }))).join(",");
const out: string[] = [];
for (const { config, events } of seqs) {
  let r = M.initial(config);
  out.push(`init ${acts(r.actions)} ${hex(M.stateBytes(r.state))}`);
  for (const e of events) {
    try {
      const n = M.step(r.state, e);
      if (n === null) {
        out.push("err Refused");
        continue;
      }
      r = n;
      out.push(`ok ${acts(r.actions)} ${hex(M.stateBytes(r.state))}`);
    } catch (e) {
      if (!(e instanceof M.StepError)) throw e;
      out.push(`err ${e.name}`);
      break;
    }
  }
  out.push("-");
}
console.log(out.join("\n"));
