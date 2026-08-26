// The config editor's own copy. The truncation notes belong here
// rather than with the agents route's delete ritual: they are about
// what its PICKERS can show, and both routes that compose a config
// render them.
export const sheetsTruncatedNote = (shown: number) => `Showing only your first ${shown} sheets.`;
export const modelsTruncatedNote = (shown: number) => `Showing only the first ${shown} models.`;
