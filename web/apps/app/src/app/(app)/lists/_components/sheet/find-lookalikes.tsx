"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Select } from "@bower/ui";
import { webRoutes, type ListSummary } from "@bower/api";

/** Seed Discover from this sheet: an inline disclosure (no modals in
 * this app) with a PLAIN column picker, empty until the user chooses.
 * Company-ness is that choice; the sheet itself stays agnostic. */
export function FindLookalikes({ detail }: { detail: ListSummary }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");

  if (detail.columns.length === 0 || detail.row_count === 0) return null;

  if (!open) {
    // The workflow verb holds the primary slot (column work takes it
    // over when fills arrive).
    return <Button onClick={() => setOpen(true)}>Find lookalikes</Button>;
  }
  return (
    <form
      className="flex items-center gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (!key) return;
        router.push(`${webRoutes.discover}?list=${encodeURIComponent(detail.id)}&key=${encodeURIComponent(key)}`);
      }}
    >
      <label htmlFor="identifier-column" className="text-sm text-muted">
        Domains column
      </label>
      <Select
        id="identifier-column"
        value={key}
        onChange={(e) => setKey(e.target.value)}
        className="w-44"
      >
        <option value="">Select a column</option>
        {detail.columns.map((column) => (
          <option key={column.key} value={column.key}>
            {column.label}
          </option>
        ))}
      </Select>
      <Button size="sm" type="submit" disabled={!key}>
        Search
      </Button>
      <Button size="sm" variant="ghost" type="button" onClick={() => setOpen(false)}>
        Cancel
      </Button>
    </form>
  );
}
