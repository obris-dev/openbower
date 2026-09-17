import type { AgentOutput, ListColumn } from "@bower/api";

// Past the family's index ON PURPOSE, and with the extension: this
// module is node-tested, and the barrel re-exports .tsx components a
// bundler-free run cannot load. Do not "fix" it to the index; that
// breaks make test-web.
import { isContentful, outputKey } from "../../../../_components/agent-config/lib/output-key.ts";

/** The column keys a fill would write: each output's OWN key (derived
 * from its label when blank, through the config editor's one mirror of the
 * server's derivation, output-key.ts), truncated to the key cap the
 * way the server stores them and deduped (two long labels can
 * truncate onto one key). Untouched trailing editor rows are not
 * outputs and land nowhere. */
export function landingKeys(outputs: AgentOutput[]): string[] {
  const keys = outputs
    .filter((output) => isContentful(output))
    .map((output) => outputKey(output))
    .filter((key) => key !== "");
  return [...new Set(keys)];
}

/** Existing column keys this fill's landing keys would hit. A hit is
 * refused at admission whether or not the column holds values, so a
 * key match is the whole answer and the client needs to know nothing
 * about emptiness (which paged rows could not tell it anyway). */
export function collidingKeys(outputs: AgentOutput[], columns: ListColumn[]): string[] {
  const existing = new Set(columns.map((column) => column.key));
  return landingKeys(outputs).filter((key) => existing.has(key));
}
