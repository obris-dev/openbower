// What a sheet's kind lets a person do to it, decided once: every surface
// that offers a sheet's verbs (home's row menu, the sheet page) reads this
// rather than comparing kinds itself.

import { webRoutes } from "./routes.ts";
import { SHEET_KIND, UNKNOWN_SHEET_KIND, type SheetKind } from "./sheets.ts";

export type SheetVerbs = {
  rename: boolean;
  delete: boolean;
  move: boolean;
  /** Rows a person adds: by hand, a CSV, or a push through the API. */
  addRows: boolean;
  addColumns: boolean;
  exportCsv: boolean;
  /** Where the sheet's own settings live, when something else owns it. */
  settingsHref: string | null;
};

const NO_VERBS: SheetVerbs = {
  rename: false,
  delete: false,
  move: false,
  addRows: false,
  addColumns: false,
  exportCsv: false,
  settingsHref: null,
};

export function sheetVerbs(sheet: { id: string; kind: SheetKind }): SheetVerbs {
  switch (sheet.kind) {
    case SHEET_KIND.STANDARD:
      return {
        rename: true,
        delete: true,
        move: true,
        addRows: true,
        addColumns: true,
        exportCsv: true,
        settingsHref: null,
      };
    // A signal's sheet: its name and life are its signal's, and its rows
    // arrive from the signal alone.
    case SHEET_KIND.LOG:
      return {
        rename: false,
        delete: false,
        move: true,
        addRows: false,
        addColumns: true,
        exportCsv: true,
        settingsHref: webRoutes.signal(sheet.id),
      };
    case UNKNOWN_SHEET_KIND:
      return NO_VERBS;
  }
}

/** Whether a sheet offers any verb at all: a sheet that offers none
 * shows no menu. */
export function offersAnyVerb(verbs: SheetVerbs): boolean {
  return Object.values(verbs).some(Boolean);
}
