import { THEME_COOKIE_NAME, THEME_STORAGE_KEY } from "./constants";

// A blocking inline script rendered first in <body>: it reads the theme
// cookie (falling back to localStorage, then the OS preference) and applies
// `.dark` on <html> BEFORE first paint, so there is no light/dark flash.
// The trusted constants are interpolated in; no user input touches this.
//
// When the cookie carries an EXPLICIT choice (a sibling site's toggle), it
// is also written into THEME_STORAGE_KEY pre-paint, so next-themes hydrates
// already agreeing instead of repainting from this origin's stale value (a
// visible flash). The cookie never holds "system", so a system-preference
// user is never pinned and OS day/night switching keeps working.
export function ThemeHeadScript() {
  const js = `
(function () {
  try {
    var m = document.cookie.match(/(?:^|; )${THEME_COOKIE_NAME}=([^;]+)/);
    var c = m ? m[1] : null;
    var t = c;
    if (c === "light" || c === "dark") {
      if (localStorage.getItem("${THEME_STORAGE_KEY}") !== c) localStorage.setItem("${THEME_STORAGE_KEY}", c);
    } else {
      t = localStorage.getItem("${THEME_STORAGE_KEY}");
    }
    if (t !== "light" && t !== "dark") {
      t = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    if (t === "dark") document.documentElement.classList.add("dark");
  } catch (e) {}
})();
`;
  return <script dangerouslySetInnerHTML={{ __html: js }} />;
}
