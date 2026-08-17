import type { ComponentProps } from "react";
import { EmptyState } from "./empty-state";

/** EmptyState with the standard page shell around it: error
 * boundaries, not-found pages, any full-page say-what-happened. */
export function PageState(props: ComponentProps<typeof EmptyState>) {
  return (
    <div className="p-6">
      <div className="mx-auto mt-16 max-w-md">
        <EmptyState {...props} />
      </div>
    </div>
  );
}
