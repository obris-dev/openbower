// Takes the README's screenshots: still pages of a signed-in app, at 2x,
// light theme. Run through scripts/demo/readme-shots.sh, which turns the
// PNGs into the .webp files in assets/readme/; never a make target
// (re-taking the README assets is a rare, maintainer-only act).
//
// READ-ONLY: the script opens pages and screenshots them. It creates,
// edits and runs nothing, so it spends nothing; set up the sheets and
// agents it shoots in the app first. Opening a cell's details is a
// press on the cell, which reads and writes nothing.
//
// Sign-in is by hand: a visible browser opens on the app's login page,
// you sign in there, and the script carries on headless with that
// session, in memory only. It never sees or stores a password. Google
// refuses a browser Playwright drives; sign in with email and password
// or GitHub, or reuse a session you already have with SHOT_COOKIES.
//
// Env (each shot is skipped, with a note, when its id is unset):
//   SHOT_SIGNAL         a signal's id; shoots its log (the hero) and its
//                       configuration page (the two share one id)
//   SHOT_AGENT          an agent's id; shoots its builder
//   SHOT_SEND_SHEET     a sheet with a Send column; shoots that sheet
//   SHOT_HERO_COLUMN    a column label on the signal's log to bring into
//                       view first (default: none, the log as it opens)
//   SHOT_DETAILS        <row id>:<column key> on the signal's log; shoots
//                       the log with that cell's details panel open and
//                       the cell's column against the panel
//   SHOT_SEND_COLUMN    the same for the Send sheet
//   SHOT_COOKIES        a file from a browser already signed in to APP_URL:
//                       the session cookie's value alone, or a JSON object
//                       of {"cookie name": "value"}; skips the sign-in
//                       window. Kept out of the repo; never printed.
//   APP_URL             default https://app.openbower.ai
//   SHOT_OUT            directory the PNGs land in (required; the shell
//                       script passes a temp dir)

import { readFileSync } from "node:fs";

import { chromium, type Cookie, type Page } from "playwright";

const APP_URL = (process.env.APP_URL || "https://app.openbower.ai").replace(/\/$/, "");
// The README shows each shot at full width; 16:9 at 2x keeps small type
// sharp after GitHub scales it down.
const VIEWPORT = { width: 1440, height: 810 };
const SCALE = 2;
// The app reads the theme from this cookie before paint
// (@bower/ui theme/constants.ts THEME_COOKIE_NAME).
const THEME_COOKIE = "openbower-theme";
const SIGN_IN_TIMEOUT_MS = 5 * 60_000;
// Beat after the network settles, for fonts and the grid's measure.
const SETTLE_MS = 1_500;
// A custom signal's page can show its webhook URL, which is a
// credential: a shot whose page carries one is refused, never written.
const CREDENTIAL_MARKERS = ["/ingest/"];
// The app's session cookie (auth_client/constants.py SESSION_COOKIE_NAME),
// for a SHOT_COOKIES file that holds its value alone.
const SESSION_COOKIE = "bwr_session";

interface Shot {
  name: string;
  path: string;
  column?: string;
  // A cell whose details panel is open in the shot.
  details?: { rowId: string; columnKey: string };
}

function detailsCell(value: string | undefined): Shot["details"] {
  if (!value) return undefined;
  const [rowId, columnKey] = value.split(":");
  if (!rowId || !columnKey) {
    console.error("readme-shots: SHOT_DETAILS is <row id>:<column key>");
    process.exit(2);
  }
  return { rowId, columnKey };
}

function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    console.error(`readme-shots: ${name} is required (see the header of this script)`);
    process.exit(2);
  }
  return value;
}

const OUT_DIR = required("SHOT_OUT");

function plannedShots(): Shot[] {
  const signal = process.env.SHOT_SIGNAL;
  const agent = process.env.SHOT_AGENT;
  const sendSheet = process.env.SHOT_SEND_SHEET;
  const shots: Shot[] = [];
  if (signal) {
    shots.push({ name: "hero", path: `/sheets/${signal}`, column: process.env.SHOT_HERO_COLUMN });
    shots.push({ name: "signal", path: `/signals/${signal}` });
    const details = detailsCell(process.env.SHOT_DETAILS);
    if (details) {
      shots.push({ name: "details", path: `/sheets/${signal}`, details });
    } else {
      console.error("readme-shots: SHOT_DETAILS unset; skipping the cell details");
    }
  } else {
    console.error("readme-shots: SHOT_SIGNAL unset; skipping the hero and the signal page");
  }
  if (agent) {
    shots.push({ name: "agent", path: `/agents/${agent}` });
  } else {
    console.error("readme-shots: SHOT_AGENT unset; skipping the agent");
  }
  if (sendSheet) {
    shots.push({ name: "send", path: `/sheets/${sendSheet}`, column: process.env.SHOT_SEND_COLUMN });
  } else {
    console.error("readme-shots: SHOT_SEND_SHEET unset; skipping the Send column");
  }
  return shots;
}

// The session a signed-in browser already holds, read from a file the
// person exported: a cookie for the app's host per name.
function cookiesFromFile(path: string, host: string): Cookie[] {
  const text = readFileSync(path, "utf8").trim();
  let parsed: unknown;
  if (text.startsWith("{")) {
    try {
      parsed = JSON.parse(text);
    } catch (error) {
      throw new Error(`SHOT_COOKIES could not be read as JSON (${error instanceof Error ? error.name : "error"})`);
    }
  } else if (text && !/\s/.test(text)) {
    parsed = { [SESSION_COOKIE]: text };
  } else {
    throw new Error(`SHOT_COOKIES holds neither a ${SESSION_COOKIE} value nor a JSON object`);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error('SHOT_COOKIES must hold an object of {"cookie name": "value"}');
  }
  const entries = Object.entries(parsed as Record<string, unknown>);
  if (entries.length === 0 || entries.some(([, value]) => typeof value !== "string" || !value)) {
    throw new Error("SHOT_COOKIES needs at least one cookie, each value a non-empty string");
  }
  return entries.map(([name, value]) => ({
    name,
    value: value as string,
    domain: host,
    path: "/",
    expires: -1,
    httpOnly: true,
    secure: APP_URL.startsWith("https:"),
    sameSite: "Lax",
  }));
}

async function signIn(): Promise<Cookie[]> {
  const browser = await chromium.launch({ headless: false });
  try {
    const context = await browser.newContext({ viewport: VIEWPORT });
    const page = await context.newPage();
    await page.goto(`${APP_URL}/login`);
    console.error("readme-shots: sign in in the browser window that opened");
    await page.waitForURL((url) => url.origin === APP_URL && !url.pathname.startsWith("/login"), {
      timeout: SIGN_IN_TIMEOUT_MS,
    });
    return await context.cookies();
  } finally {
    await browser.close();
  }
}

// Scrolls the grid so the column sits first after the sticky gutter.
async function bringColumn(page: Page, label: string): Promise<void> {
  const menu = page.getByRole("button", { name: `${label} column menu`, exact: true });
  if ((await menu.count()) === 0) {
    throw new Error(`no column labeled "${label}" on ${page.url()}`);
  }
  await menu.first().evaluate((button) => {
    // The grid's own scroll region: a header cell can overflow a few
    // pixels, so overflow alone does not name the scroller.
    const scrolls = (el: HTMLElement) =>
      ["auto", "scroll"].includes(getComputedStyle(el).overflowX) && el.scrollWidth > el.clientWidth;
    let scroller: HTMLElement | null = button.parentElement;
    while (scroller && !scrolls(scroller)) scroller = scroller.parentElement;
    if (!scroller) return;
    const gutter = scroller.querySelector<HTMLElement>(".sticky.left-0");
    const gutterWidth = gutter ? gutter.getBoundingClientRect().width : 0;
    // The header cell (header-cell.tsx) is the menu button's nearest
    // bordered, fixed-width ancestor.
    const cell = button.closest(".flex-none.border-r") ?? button;
    const scrollerLeft = scroller.getBoundingClientRect().left;
    const left = cell.getBoundingClientRect().left - scrollerLeft + scroller.scrollLeft;
    scroller.scrollLeft = left - gutterWidth;
  });
}

// A press on the cell, as a person opens it: the panel docks beside the
// grid, then its details load (the reasoning replaces the skeleton). The
// panel narrows the grid, which can push the cell out of view, so the
// cell is then scrolled to the grid's edge beside the panel.
async function openDetails(page: Page, cell: NonNullable<Shot["details"]>): Promise<void> {
  const target = page.locator(`[data-row-id="${cell.rowId}"][data-column-key="${cell.columnKey}"]`).first();
  if ((await target.count()) === 0) {
    throw new Error(`no cell ${cell.rowId}:${cell.columnKey} on ${page.url()}`);
  }
  await target.scrollIntoViewIfNeeded();
  await target.click({ position: { x: 4, y: 4 } });
  const panel = page.locator("aside[aria-label$=' details']");
  await panel.waitFor();
  await page.waitForLoadState("networkidle");
  await panel.locator(".animate-pulse").first().waitFor({ state: "detached" }).catch(() => undefined);
  await target.evaluate((el) => el.scrollIntoView({ block: "nearest", inline: "end" }));
  const firstWhole = await page.evaluate(() => {
    const gutter = document.querySelector<HTMLElement>(".sticky.left-0");
    const edge = gutter ? gutter.getBoundingClientRect().right : 0;
    const menus = [...document.querySelectorAll<HTMLElement>("button[aria-label$=' column menu']")];
    const whole = menus.find((menu) => (menu.closest(".flex-none.border-r") ?? menu).getBoundingClientRect().left >= edge - 1);
    return whole?.getAttribute("aria-label")?.replace(/ column menu$/, "") ?? null;
  });
  // A column cut by the gutter shows half its words and no header, so the
  // first whole column starts the view; the cut moves to the right edge.
  if (firstWhole) await bringColumn(page, firstWhole);
}

async function take(page: Page, shot: Shot): Promise<string> {
  await page.goto(`${APP_URL}${shot.path}`);
  await page.waitForLoadState("networkidle");
  if (new URL(page.url()).pathname.startsWith("/login")) {
    throw new Error(`${shot.path} sent the session back to the login page`);
  }
  if (shot.column) await bringColumn(page, shot.column);
  if (shot.details) await openDetails(page, shot.details);
  await page.mouse.move(0, VIEWPORT.height - 1);
  await page.waitForTimeout(SETTLE_MS);
  const html = await page.content();
  if (CREDENTIAL_MARKERS.some((marker) => html.includes(marker))) {
    throw new Error(`${shot.path} shows a webhook URL; not written (shoot a page without one)`);
  }
  const file = `${OUT_DIR}/${shot.name}.png`;
  await page.screenshot({ path: file });
  return file;
}

async function main(): Promise<void> {
  const shots = plannedShots();
  if (shots.length === 0) {
    console.error("readme-shots: nothing to shoot; set SHOT_SIGNAL, SHOT_AGENT or SHOT_SEND_SHEET");
    process.exit(2);
  }
  const host = new URL(APP_URL).hostname;
  const cookieFile = process.env.SHOT_COOKIES;
  const cookies = cookieFile ? cookiesFromFile(cookieFile, host) : await signIn();
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({
      viewport: VIEWPORT,
      deviceScaleFactor: SCALE,
      colorScheme: "light",
      reducedMotion: "reduce",
    });
    await context.addCookies([...cookies, { name: THEME_COOKIE, value: "light", domain: host, path: "/" }]);
    const page = await context.newPage();
    for (const shot of shots) {
      const file = await take(page, shot);
      console.error(`readme-shots: ${shot.name} from ${shot.path}`);
      console.log(file);
    }
  } finally {
    await browser.close();
  }
}

main().catch((error: unknown) => {
  console.error(`readme-shots: ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
});
