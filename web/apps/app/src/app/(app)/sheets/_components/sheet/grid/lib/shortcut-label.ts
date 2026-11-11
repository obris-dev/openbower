/** The modifier a shortcut is shown with: the command glyph where the
 * command key is the modifier, Ctrl elsewhere. Shown only, never
 * bound (the surface binds metaKey or ctrlKey, whichever the platform
 * has). */
export function shortcutLabel(key: string, platform: string): string {
  const command = /mac|iphone|ipad|ipod/i.test(platform);
  return command ? `⌘${key}` : `Ctrl+${key}`;
}
