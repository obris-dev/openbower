// The Worker entry, wrapping the OpenNext-generated handler.
//
// A `curl` or `wget` request for the root path gets the installer, so
// the documented one-liner works:
//
//   curl -fsSL https://openbower.ai | sh
//
// The script is scripts/install.sh at the repo root, copied into
// public/ by the build (the `copy-installer` script), never embedded
// here: one source, and a release's script is the one served. It is
// also a plain asset at /install.sh for anyone who reads it first.
// Every other request (a browser, a crawler) goes to the Next.js site.
import worker from "./.open-next/worker.js";

const handler = {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const ua = (request.headers.get("user-agent") || "").toLowerCase();

    if (url.pathname === "/" && (ua.startsWith("curl") || ua.startsWith("wget"))) {
      // The asset's own status rides through, so a missing install.sh
      // is a 404 rather than a 200 with an empty body piped into sh.
      // This is a fresh Response, so the _headers rule for the asset
      // does not apply to it; no-store is set here for the same reason.
      const script = await env.ASSETS.fetch(new URL("/install.sh", request.url));
      return new Response(script.body, {
        status: script.status,
        headers: {
          "content-type": "text/plain; charset=utf-8",
          "cache-control": "no-store",
        },
      });
    }

    return worker.fetch(request, env, ctx);
  },
};

export default handler;
