// One absolute timestamp format for delivery times. Pinned locale and
// UTC: the roster is server-rendered AND hydrated, and a relative or
// local-zone rendering would disagree between the two (a hydration
// error), so the log reads in UTC with seconds, which is also what a
// receiver's own log shows.

export function formatTime(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const text = at.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "UTC",
  });
  return `${text} UTC`;
}
