// The generated Svelte module, compiled and run headless (docs/spec.md §3.18.4, §3.18.5).
//
//   deno run -A svelte.ts <module.svelte.ts> <sequences.json>
//
// Strips the types (TypeScript's transpiler), compiles the result with Svelte's compileModule (a warning
// fails), imports it, and drives one machine per sequence through its input methods with a port that
// records its calls, next to the plain module's `step`. Per event, the method's result must say whether
// `step` accepted the event, the port calls must be the step's actions in order, the view must hold the
// step's state, an effect that reads the view must have run again exactly when the state changed, and an
// effect that reads allowed() must hold its current value.
// Prints "ok <events>" or the first difference.
import ts from "npm:typescript@5.9.3";
import { compileModule } from "npm:svelte@5.57.2/compiler";
// The client build: under Deno "svelte" itself resolves to the server build, whose flushSync does nothing.
import { flush as flushSync } from "npm:svelte@5.57.2/internal/client";

const [modPath, seqPath] = Deno.args;
const plainPath = modPath.replace(/\.svelte\.ts$/, ".ts");
const M = await import("file://" + plainPath);
const dir = await Deno.makeTempDir({ prefix: "naima-svelte-" });

async function compile(path: string, src: string): Promise<string> {
  const js = ts.transpileModule(src, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
    .outputText;
  const r = compileModule(js, { filename: path, generate: "client" });
  if (r.warnings.length) throw new Error("svelte warnings: " + r.warnings.map((w) => w.message).join("; "));
  const out = dir + "/" + path.split("/").pop()!.replace(/\.ts$/, ".js");
  await Deno.writeTextFile(out, r.js.code.replaceAll(/(["'])svelte\/internal\/client\1/g, '"npm:svelte@5.57.2/internal/client"')
    .replaceAll(/(["'])svelte\1/g, '"npm:svelte@5.57.2"')
    .replaceAll(/(["'])\.\/([^"']+\.ts)\1/g, (_m, _q, f) => JSON.stringify("file://" + plainPath.replace(/[^/]+$/, f))));
  return out;
}

const S = await import("file://" + await compile(modPath, await Deno.readTextFile(modPath)));
const W = await import("file://" + await compile(dir + "/watch.svelte.ts", `
export function watch(read: () => unknown, seen: unknown[]): () => void {
  return $effect.root(() => {
    $effect.pre(() => {
      seen.push(read());
    });
  });
}`));
const Machine = Object.values(S).find((x) => typeof x === "function" && /^class /.test(String(x)) &&
  "allowed" in (x as { prototype: object }).prototype) as new (c: unknown, p: unknown) => Record<string, unknown>;
const seqs = JSON.parse(await Deno.readTextFile(seqPath));
const hex = (b: Uint8Array) => [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
let n = 0;
const fail = (msg: string) => {
  console.log(msg);
  Deno.removeSync(dir, { recursive: true });
  Deno.exit(0);
};
for (const [k, { config, events }] of seqs.entries()) {
  const calls: unknown[] = [];
  const port = new Proxy({}, {
    get: (_t, name) => (...args: unknown[]) => calls.push([String(name), ...args]),
  });
  const machine = new Machine(config, port);
  const view = machine.view as Record<string, unknown>;
  const seen: unknown[] = [];
  const stop = W.watch(() => hex(M.stateBytes(view)), seen);
  const allowedNow = () => events.map((x: unknown) => machine.allowed(x)).join();
  const seenAllowed: unknown[] = [];
  const stopAllowed = W.watch(allowedNow, seenAllowed);
  flushSync();
  let r = M.initial(config);
  const expectCalls = (actions: Record<string, unknown>[]) =>
    actions.map((a) => {
      const t = M.outputs.find((o: { type: string }) => o.type === a.type);
      return [a.type === "refused" ? "refused_" : a.type, ...t.args.map((x: { name: string }) => a[x.name])];
    });
  if (JSON.stringify(calls) !== JSON.stringify(expectCalls(r.actions))) fail(`sequence ${k}: the constructor's port calls`);
  for (const [i, e] of events.entries()) {
    calls.length = 0;
    const before = seen.length;
    const next = M.step(r.state, e);
    const t = M.inputs.find((x: { type: string }) => x.type === e.type);
    const method = ["view", "allowed", "stopped", "constructor"].includes(e.type) ? e.type + "_" : e.type;
    const allowed = machine.allowed(e);
    const got = (machine[method] as (...a: unknown[]) => boolean)(...t.args.map((x: { name: string }) => e[x.name]));
    flushSync();
    const where = `sequence ${k} event ${i} ${JSON.stringify(e)}`;
    if (got !== (next !== null) || allowed !== got) fail(`${where}: the method said ${got}, allowed ${allowed}, step ${next !== null}`);
    if (next === null) {
      if (JSON.stringify(calls) !== JSON.stringify([["refused", e]])) fail(`${where}: a refusal reported as ${JSON.stringify(calls)}`);
      continue;
    }
    if (JSON.stringify(calls) !== JSON.stringify(expectCalls(next.actions))) fail(`${where}: port calls ${JSON.stringify(calls)}`);
    if (hex(M.stateBytes(view)) !== hex(M.stateBytes(next.state))) fail(`${where}: the view differs from the step's state`);
    const changed = hex(M.stateBytes(next.state)) !== hex(M.stateBytes(r.state));
    if (changed !== (seen.length > before)) fail(`${where}: the effect ran ${seen.length - before} times, the state ${changed ? "changed" : "did not change"}`);
    if (seenAllowed[seenAllowed.length - 1] !== allowedNow()) fail(`${where}: an effect reading allowed() was not run again`);
    r = next;
    n += 1;
  }
  stop();
  stopAllowed();
}
Deno.removeSync(dir, { recursive: true });
console.log("ok " + n);
