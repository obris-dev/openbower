// Records the README's sheet tour: a sheet with two agent columns, rows
// landing, their cells resolving, then a hold. Run through
// scripts/demo/sheet-record.sh, which turns the video into the GIF;
// never a make target (re-recording the README asset is a rare,
// maintainer-only act).
//
// The recording shows SCREENS ONLY: a headless page has no browser
// chrome, so no URL bar or tabs ever appear. Two contexts keep the
// login and the setup out of the video: the first signs in and builds
// the sheet through the API, the second inherits its session and is the
// one recorded, opening on the finished sheet before the rows arrive.
//
// Needs a running local stack (make up) with an identity provider the
// app can log in against, and a model the agents can reach. Env:
//   TOUR_EMAIL, TOUR_PASSWORD   the account to sign in as (required)
//   TOUR_MODEL                  a model on the deploy's catalog; default
//                               the first one from the "ollama" source,
//                               which may be the slowest you have pulled:
//                               name a small one (a 7B to 12B model
//                               answers a row in seconds, a 26B one in
//                               minutes at Ollama's default of one
//                               request at a time)
//   TOUR_SETTLE_MINUTES         how long to wait for every cell to
//                               settle before giving up; default 10
//   TOUR_TOOLS                  "none" runs the agents with no tools (a
//                               dry run that spends nothing; the cells
//                               then land as blanks with reasons). Unset,
//                               each agent uses the tools it declares
//                               below, which go through the vendors
//                               wired in config/tools.toml, so a metered
//                               vendor is spent: a few searches per row
//   TOUR_ROWS                   comma-separated company domains for the
//                               rows; default four widely known
//                               companies, since the research has to be
//                               findable for the cells to show anything
//                               real (a made-up domain yields blanks)
//   TOUR_LAUNCH=1               add the third agent, a launch tracker
//                               (off by default: from search snippets
//                               alone the model rarely clears the
//                               verification floor on a dated launch,
//                               so most of its cells land unverified)
//   TOUR_KEEP=1                 leave the sheet and agents in place
//   APP_URL, API_URL            defaults http://localhost:3003 / :8002
//   TOUR_OUT                    directory the .webm lands in (required;
//                               the shell script passes a temp dir)
//   TOUR_HEADED=1               open a visible browser (debugging the
//                               login or the sheet); the video still
//                               records the page only
//
// A login that never leaves /login writes login-failure.png into
// TOUR_OUT and prints the form's error text, so the cause (no such
// account, a wrong password, an identity provider that is down) is
// on the terminal rather than behind a timeout.

import { chromium, type BrowserContext, type Cookie } from "playwright";

const APP_URL = (process.env.APP_URL || "http://localhost:3003").replace(/\/$/, "");
const API_URL = (process.env.API_URL || "http://localhost:8002").replace(/\/$/, "");
const API = `${API_URL}/v1`;

// Wide enough for five columns of real answers beside the collapsed
// sidebar; the GIF is scaled down from this.
const VIEWPORT = { width: 1440, height: 810 };
// The app's sidebar collapse is a localStorage flag its head script
// reads before paint; set here so the recording opens on the rail.
const SIDEBAR_COLLAPSE_KEY = "bower.sidebar.collapsed";
// How long the recording waits for every cell to settle before giving
// up: runs on a local model go one at a time, so four rows and two
// agents is eight model calls in sequence.
const SETTLE_TIMEOUT_MS = Number(process.env.TOUR_SETTLE_MINUTES || "10") * 60_000;
const POLL_MS = 1_500;
// Beats of the recording (ms): the empty sheet before rows land, and
// the settled sheet at the end, so the GIF opens and closes at rest.
const OPENING_HOLD_MS = 2_000;
const CLOSING_HOLD_MS = 3_000;

const SHEET_LABEL = "Q4 outbound list";
const COMPANY_KEY = "company";
// Real, widely covered companies: the recording is only honest if the
// details on screen are findable. Override with TOUR_ROWS.
const ROWS = (process.env.TOUR_ROWS || "stripe.com,notion.so,linear.app,vercel.com")
  .split(",")
  .map((domain) => domain.trim())
  .filter(Boolean);

// Two agents by default, each the expert on one detail, in the README's
// words; a third behind TOUR_LAUNCH.
// Prompts reference the sheet's one plain column by key. The person
// agent owns two related columns (the name and the profile), which is
// the roster's one-agent-several-columns shape, and asks for the CEO
// or founder: a public figure, since the recording ends up in a
// README. It needs the contacts tool (the profile x-ray) and web
// search; the funding agent needs web search alone.
const AGENTS = [
  {
    label: "Decision-maker finder",
    prompt:
      "Find the CEO or founder of {{company}}: their full name, and their LinkedIn profile URL taken exactly from the contacts result that names them.",
    tools: ["find_contacts", "web_search"],
    outputs: [
      { key: "decision_maker", label: "Decision maker", type: "text" },
      { key: "linkedin", label: "LinkedIn", type: "url" },
    ],
  },
  {
    // The qualification column, answerable from the company's own
    // site. (A funding round was tried first: ambiguous for any
    // late-stage company, so the model hedged and the verification
    // floor dropped it.)
    label: "Fit researcher",
    prompt: "What does {{company}} sell, and to whom? One line each.",
    tools: ["web_search"],
    outputs: [
      { key: "what_they_sell", label: "What they sell", type: "text" },
      { key: "who_they_sell_to", label: "Who they sell to", type: "text" },
    ],
  },
];

const LAUNCH_AGENT = [
  {
    // The "why now": a dated launch from the company's own blog or
    // changelog, which the default rows all publish monthly.
    label: "Launch tracker",
    // "A launch in the last 60 days", not "the most recent": the
    // superlative made the model hedge on which was newest and the
    // verification floor dropped every answer.
    prompt:
      "Find one product launch or feature that {{company}} announced in the last 60 days on its own blog or changelog. Give its name and the date.",
    tools: ["web_search"],
    outputs: [{ key: "latest_launch", label: "Latest launch", type: "text" }],
  },
];
const TOUR_AGENTS = process.env.TOUR_LAUNCH === "1" ? [...AGENTS, ...LAUNCH_AGENT] : AGENTS;

function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    console.error(`sheet-tour: ${name} is required (see the header of this script)`);
    process.exit(2);
  }
  return value;
}

const EMAIL = required("TOUR_EMAIL");
const PASSWORD = required("TOUR_PASSWORD");
const OUT_DIR = required("TOUR_OUT");
const NO_TOOLS = process.env.TOUR_TOOLS === "none";

type Api = <T>(method: "GET" | "POST" | "DELETE", path: string, body?: unknown) => Promise<T>;

function apiWith(cookies: Cookie[]): Api {
  const cookie = cookies.map((c) => `${c.name}=${c.value}`).join("; ");
  async function call<T>(method: "GET" | "POST" | "DELETE", path: string, body?: unknown): Promise<T> {
    const res = await fetch(`${API}${path}`, {
      method,
      headers: { cookie, "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`${method} ${path} answered ${res.status}: ${text.slice(0, 300)}`);
    }
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }
  return call;
}

interface CatalogModel {
  provider: string;
  source: string;
  model: string;
}

async function pickModel(api: Api): Promise<CatalogModel> {
  const catalog = await api<{ models: CatalogModel[] }>("GET", "/agents/catalog");
  const wanted = process.env.TOUR_MODEL;
  const found = wanted
    ? catalog.models.find((m) => m.model === wanted)
    : catalog.models.find((m) => m.source === "ollama") ?? catalog.models[0];
  if (!found) {
    throw new Error(
      wanted
        ? `TOUR_MODEL=${wanted} is not on the catalog (${catalog.models.map((m) => m.model).join(", ")})`
        : "the catalog has no models; configure config/providers.toml",
    );
  }
  return found;
}

async function login(context: BrowserContext): Promise<Cookie[]> {
  const page = await context.newPage();
  await page.goto(`${APP_URL}/login`);
  await page.fill("#email", EMAIL);
  await page.fill("#password", PASSWORD);
  await page.click('button[type="submit"]');
  // The login rides the identity provider's redirect dance and lands
  // back on the app; wait for a URL on the app origin that is not the
  // login page.
  try {
    await page.waitForURL((url) => url.origin === APP_URL && !url.pathname.startsWith("/login"), {
      timeout: 60_000,
    });
  } catch (error: unknown) {
    const shot = `${OUT_DIR}/login-failure.png`;
    await page.screenshot({ path: shot, fullPage: true });
    const errors = await page.locator('[role="alert"], [id$="-error"]').allInnerTexts();
    const where = page.url();
    throw new Error(
      `login did not leave ${where} in 60s` +
        (errors.length ? `; the form says: ${errors.join(" | ")}` : "; the form shows no error") +
        `; screenshot at ${shot}` +
        ` (${error instanceof Error ? error.message.split("\n")[0] : String(error)})`,
    );
  }
  const cookies = await context.cookies();
  if (!cookies.some((c) => c.name === "bwr_session")) {
    throw new Error("signed in, but no bwr_session cookie was set; is the app pointed at this API?");
  }
  await page.close();
  return cookies;
}

interface Built {
  listId: string;
  agentIds: string[];
  aiKeys: string[];
}

async function buildSheet(api: Api): Promise<Built> {
  const model = await pickModel(api);
  console.error(`sheet-tour: agents run on ${model.model} (${model.source}); set TOUR_MODEL to choose another`);
  const agentIds: string[] = [];
  for (const agent of TOUR_AGENTS) {
    const tools = NO_TOOLS ? {} : Object.fromEntries(agent.tools.map((tool) => [tool, true]));
    const created = await api<{ id: string }>("POST", "/agents", {
      label: agent.label,
      config: {
        prompt: agent.prompt,
        provider: model.provider,
        source: model.source,
        model: model.model,
        tools,
        outputs: agent.outputs,
      },
    });
    agentIds.push(created.id);
  }
  const list = await api<{ id: string }>("POST", "/lists", {
    label: SHEET_LABEL,
    columns: [{ key: COMPANY_KEY, label: "Company", type: "text" }],
  });
  for (const agentId of agentIds) {
    await api("POST", `/lists/${list.id}/columns/ai`, { agent_id: agentId });
  }
  return { listId: list.id, agentIds, aiKeys: TOUR_AGENTS.flatMap((a) => a.outputs.map((o) => o.key)) };
}

interface RowWire {
  id: string;
  data: Record<string, string>;
  states: Record<string, { state: string }>;
}

function settled(row: RowWire, key: string): boolean {
  if (row.data[key]) return true;
  const state = row.states[key]?.state;
  return state !== undefined && state !== "pending";
}

async function waitForCells(api: Api, listId: string, aiKeys: string[], expected: number): Promise<void> {
  const deadline = Date.now() + SETTLE_TIMEOUT_MS;
  const total = expected * aiKeys.length;
  let sawWork = false;
  let lastReported = -1;
  while (Date.now() < deadline) {
    const page = await api<{ items: RowWire[] }>("GET", `/lists/${listId}/rows?limit=200`);
    const rows = page.items;
    const touched = rows.some((row) => aiKeys.some((key) => key in row.data || key in row.states));
    sawWork = sawWork || touched;
    const settledCount = rows.reduce((n, row) => n + aiKeys.filter((key) => settled(row, key)).length, 0);
    if (settledCount !== lastReported) {
      console.error(`sheet-tour: ${settledCount} of ${total} cells settled`);
      lastReported = settledCount;
    }
    const done = rows.length >= expected && settledCount === total;
    if (sawWork && done) return;
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
  throw new Error(
    `the cells did not settle within ${SETTLE_TIMEOUT_MS / 60_000} minutes (${lastReported} of ${total} did); ` +
      "a slow model or a stalled worker (check make logs-autofill); TOUR_SETTLE_MINUTES raises the wait",
  );
}

async function teardown(api: Api, built: Built): Promise<void> {
  // The sheet first (its columns hold the agents), then the agents.
  await api("DELETE", `/lists/${built.listId}`);
  for (const agentId of built.agentIds) await api("DELETE", `/agents/${agentId}`);
}

async function main(): Promise<void> {
  const browser = await chromium.launch({ headless: process.env.TOUR_HEADED !== "1" });
  let built: Built | null = null;
  let api: Api | null = null;
  // A killed run must still tear down: a sheet left behind keeps its
  // queued runs in front of the next recording's on a one-at-a-time
  // worker, so the next run starves on this one's leftovers.
  const onSignal = async (signal: string) => {
    console.error(`sheet-tour: ${signal}; tearing down before exit`);
    try {
      if (built && api && process.env.TOUR_KEEP !== "1") await teardown(api, built);
    } finally {
      await browser.close();
      process.exit(130);
    }
  };
  process.once("SIGINT", () => void onSignal("SIGINT"));
  process.once("SIGTERM", () => void onSignal("SIGTERM"));
  try {
    const setup = await browser.newContext({ viewport: VIEWPORT });
    const cookies = await login(setup);
    await setup.close();
    api = apiWith(cookies);
    built = await buildSheet(api);
    console.error(`sheet-tour: built sheet ${built.listId} with ${built.agentIds.length} agents`);

    const recording = await browser.newContext({
      viewport: VIEWPORT,
      deviceScaleFactor: 2,
      recordVideo: { dir: OUT_DIR, size: VIEWPORT },
    });
    await recording.addCookies(cookies);
    await recording.addInitScript(([k, v]) => window.localStorage.setItem(k, v), [SIDEBAR_COLLAPSE_KEY, "1"]);
    const page = await recording.newPage();
    await page.goto(`${APP_URL}/lists/${built.listId}`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(OPENING_HOLD_MS);

    // The rows arrive while the sheet is on screen. A sheet with no rows
    // shows a one-line empty state and has no pending cell to poll for,
    // so it would never learn the rows landed on its own: one reload
    // right after the post brings the grid in with its cells pending,
    // and from there the page's own refresh follows them to settled.
    await api("POST", `/lists/${built.listId}/rows`, { rows: ROWS.map((company) => ({ [COMPANY_KEY]: company })) });
    await page.reload({ waitUntil: "networkidle" });
    await waitForCells(api, built.listId, built.aiKeys, ROWS.length);
    // The page's own refresh backs off while cells are pending, so the
    // last cell can settle on the server a beat before the page shows
    // it; one reload lands the finished sheet before the closing hold.
    await page.reload({ waitUntil: "networkidle" });
    await page.waitForTimeout(CLOSING_HOLD_MS);

    const video = page.video();
    await page.close();
    await recording.close();
    const path = video ? await video.path() : null;
    console.log(path ?? "");
  } finally {
    await browser.close();
    if (built && api && process.env.TOUR_KEEP !== "1") {
      await teardown(api, built);
      console.error("sheet-tour: removed the sheet and its agents (TOUR_KEEP=1 keeps them)");
    }
  }
}

main().catch((error: unknown) => {
  console.error(`sheet-tour: ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
});
