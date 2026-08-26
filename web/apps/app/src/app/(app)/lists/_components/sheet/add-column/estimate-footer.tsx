import { FILL_ROW_ATTEMPTS, MAX_TOOL_CALLS } from "@bower/api";

/** The counts-only consent line under the submit: the calls this fill
 * may buy, spoken before they are bought. rowCount is the SERVER's
 * count (consent copy must not trust loaded pages), and every figure
 * computes from the wire constants. Counts read "up to" because a
 * timeout after generation started still bills. */
export function EstimateFooter({
  rowCount,
  model,
  source,
  toolsOn,
}: {
  rowCount: number;
  model?: string;
  source?: string;
  toolsOn: boolean;
}) {
  const rows = rowCount.toLocaleString("en-US");
  return (
    <div className="text-xs text-muted">
      <p className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
        <span className="whitespace-nowrap">{rows} rows</span>
        {model && (
          <span>
            | up to {rows} completions on {model}
            {source ? ` via your ${source} source` : ""}
          </span>
        )}
        {toolsOn && (
          <span className="whitespace-nowrap">
            | up to {(rowCount * MAX_TOOL_CALLS).toLocaleString("en-US")} searches
          </span>
        )}
      </p>
      <p className="mt-0.5 text-faint">a row whose provider is unavailable retries; worst case {FILL_ROW_ATTEMPTS}x these counts</p>
    </div>
  );
}
