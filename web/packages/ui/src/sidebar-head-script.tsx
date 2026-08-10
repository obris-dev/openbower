import { SIDEBAR_COLLAPSE_KEY } from "./sidebar-constants";

// Blocking script, ThemeHeadScript's sibling: stamps the remembered
// sidebar state on <html> BEFORE first paint. The Sidebar renders its
// collapsed/expanded variants purely from this attribute (CSS, not React
// state), so server HTML is variant-neutral and there is no
// expanded-then-snap flash while hydration catches up.
const SCRIPT = `try{if(localStorage.getItem("${SIDEBAR_COLLAPSE_KEY}")==="1")document.documentElement.setAttribute("data-sidebar","collapsed")}catch(e){}`;

export function SidebarHeadScript() {
  return <script dangerouslySetInnerHTML={{ __html: SCRIPT }} />;
}
