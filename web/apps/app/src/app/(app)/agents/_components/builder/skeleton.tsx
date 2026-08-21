/** The builder's shape while the client-only form loads (the ssr:false
 * wrapper renders nothing on the server): same grid, same card
 * heights, so the real form lands without a layout shift. Pulse
 * respects reduced motion. */
export function BuilderSkeleton() {
  const block = "animate-pulse rounded-md bg-wash motion-reduce:animate-none";
  return (
    <>
      <div className="min-w-0">
        <div className={`h-4 w-16 ${block}`} />
        <div className={`mt-2 h-8 w-56 ${block}`} />
      </div>
      <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
        <div className="min-w-0 space-y-4 rounded-lg border border-hairline p-6">
          <div className={`h-9 w-full ${block}`} />
          <div className={`h-64 w-full ${block}`} />
          <div className={`h-24 w-full ${block}`} />
        </div>
        <div className="min-w-0 space-y-4">
          <div className={`h-28 w-full ${block}`} />
          <div className={`h-48 w-full ${block}`} />
          <div className={`h-36 w-full ${block}`} />
        </div>
      </div>
    </>
  );
}
