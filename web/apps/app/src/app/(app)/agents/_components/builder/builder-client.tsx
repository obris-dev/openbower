"use client";

import dynamic from "next/dynamic";

import { BuilderSkeleton } from "./skeleton";

// The builder never server-renders (ssr: false): its whole working
// state initializes from localStorage drafts, and client-only is what
// makes a render-time draft read hydration-safe (no server HTML to
// disagree with). React.lazy + Suspense would still SSR the tree;
// dynamic's loading slot IS a Suspense fallback, holding the layout's
// shape so the form lands without a shift.
export const Builder = dynamic(() => import("./builder").then((m) => m.BuilderForm), {
  ssr: false,
  loading: () => <BuilderSkeleton />,
});
