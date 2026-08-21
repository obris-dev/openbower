// @bower/schema: zod validators generated from the shared Pydantic
// contract (see packages/openbower-schema). Run `pnpm --filter
// @bower/schema generate` after the Python side changes. A star
// re-export, deliberately: a hand-maintained list drifts against the
// generated module (the .ts extension is fine for bundlers and the
// plain-node test lane alike).
export * from "./generated.ts";
