/** What the pre-paint widths script needs to know, all of it trusted
 * app constants: which paths carry an entity id (a pattern source
 * whose FIRST capture group is the id), where that entity's widths
 * are stored (`storageKeyPrefix + id`), what to stamp them under
 * (`propertyPrefix + id + "-" + key`), and the bounds to clamp into. */
export type StoredWidthsOptions = {
  path: string;
  storageKeyPrefix: string;
  /** The custom property name up to the id, `--` included. */
  propertyPrefix: string;
  min: number;
  max: number;
};

/** The source of a blocking script, ThemeHeadScript's sibling for a
 * value CSS cannot enumerate: one storage entry of pixel widths by
 * key (`{"name": 320}`), kept per entity, stamped on <html> as custom
 * properties BEFORE first paint, so markup that sizes itself with
 * `var(--<prefix><id>-<key>, <default>px)` paints the stored width at
 * once instead of the default and then the stored one after
 * hydration. Rendered once from the root layout like its siblings, it
 * finds the entity in the URL itself, so no route value ever reaches
 * this source; on a path that carries no id it does nothing. Each
 * width is clamped into the caller's bounds, the same bounds the
 * caller's own read applies, so the two agree. Storage is writable by
 * anything on the origin, so an id or a key that is not a plain
 * identifier, a value that is not a finite number, and an array (its
 * indexes would read as keys) are skipped. */
export function storedWidthsScript({ path, storageKeyPrefix, propertyPrefix, min, max }: StoredWidthsOptions): string {
  if (!/^--[A-Za-z0-9_-]+$/.test(propertyPrefix)) throw new Error(`Not a custom property prefix: ${propertyPrefix}`);
  if (!Number.isFinite(min) || !Number.isFinite(max) || min > max) throw new Error(`Not a width range: ${min}..${max}`);
  return `
(function () {
  try {
    var pattern = new RegExp(${literal(path)});
    var match = location.pathname.match(pattern);
    if (!match) return;
    var id = match[1];
    if (!/^[A-Za-z0-9_-]+$/.test(id)) return;
    var raw = localStorage.getItem(${literal(storageKeyPrefix)} + id);
    var widths = JSON.parse(raw);
    if (!widths || typeof widths !== "object" || Array.isArray(widths)) return;
    var style = document.documentElement.style;
    for (var key in widths) {
      var width = widths[key];
      if (typeof width !== "number" || !isFinite(width) || !/^[A-Za-z0-9_-]+$/.test(key)) continue;
      width = Math.round(width);
      width = Math.max(width, ${min});
      width = Math.min(width, ${max});
      var name = ${literal(propertyPrefix)} + id + "-" + key;
      style.setProperty(name, width + "px");
    }
  } catch (e) {}
})();
`;
}

/** A string as a JS literal that cannot close the script element. */
function literal(value: string): string {
  const json = JSON.stringify(value);
  return json.replace(/</g, "\\u003c");
}
