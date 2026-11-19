import type { CellDetail, ConfidenceLevel } from "@bower/api";

// The server's confidence levels in a person's words. Typed CLOSED over
// the contract's levels, so a level added server-side fails this build
// until it has a word; a level this bundle never heard of arrives as
// null (the tolerant read) and shows no level at all.
const LEVEL_WORDS: Record<ConfidenceLevel, string> = {
  very_high: "Very high confidence",
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence",
  very_low: "Very low confidence",
};

// The confidence mark's segments, one per level, lowest to highest.
export const CONFIDENCE_BARS = 5;
const LEVEL_BARS: Record<ConfidenceLevel, number> = { very_low: 1, low: 2, medium: 3, high: 4, very_high: 5 };

/** How many of the mark's segments a level lights. */
export function litBars(level: ConfidenceLevel): number {
  return LEVEL_BARS[level];
}

/** A level in a person's words; null (a level this bundle cannot name)
 * says nothing. */
export function levelWord(level: CellDetail["level"]): string {
  return level ? LEVEL_WORDS[level] : "";
}

export const UNCONFIRMED_LABEL = "Unconfirmed answer";
export const USE_ANSWER = "Use answer";
export const NOT_ON_SHEET = "Not on the sheet yet";
export const DETAIL_FAILED = "Couldn't load this answer's details.";
export const VIEW_DETAILS = "View details";
export const YOU_SET_THIS = "You set this value.";
export const EMPTY_CELL = "Nothing in this cell yet.";
