# Layout rules

Hard-won CSS rules for every component in this package and every screen
that composes them. Violations are the top source of broken-on-mobile
reports; review against this list before adding a component.

1. **min-w-0 discipline.** Flex and grid items default to
   `min-width: auto` and refuse to shrink below their content: any
   truncate, any nowrap line, and any native control (input, select) in a
   flex/grid context needs `min-w-0` on the item (and sometimes the
   ancestor chain) before clipping can work.
2. **User content wraps with `[overflow-wrap:anywhere]`,** not
   `break-words`: `break-word` doesn't reduce intrinsic min-content
   width, so a whitespace-free token (a long URL) still forces the box
   wide and line-clamps never engage.
3. **Mobile-first rows.** A table-like row stacks on phones: the identity
   block flexes (title over a subtext line), metadata columns are
   `sm:`-and-up, actions stay pinned and reachable. Never let fixed-width
   columns push actions off the card edge.
4. **Native selects get `appearance-none` and our own chevron** with real
   right padding; background-styled native selects draw their caret
   nearly flush to the edge.
5. **`|` is the separator** in UI copy (never middle dots), and it only
   appears inside short non-wrapping segments.
6. **Drawers and inline editing, not modals.** Pinned header/footer,
   scrollable body; footer buttons submit body forms via the `form`
   attribute.
7. **Pre-paint state via head scripts.** Anything the server can't know
   (theme, collapsed chrome) is stamped on `<html>` by a blocking script
   and rendered from CSS keyed on that attribute; lazy `useState` cannot
   beat SSR to the first paint.
8. **Behavioral components ride Headless UI** (menus, dialogs, switches,
   comboboxes); hand-rolled focus/keyboard handling is a bug factory.
9. **Errors have three tiers; pick by attachment, not preference.**
   (a) Field-level validation lives at its field (a mismatch hint, an
   invalid email), often live. (b) A submission failure not attributable
   to one field (invalid credentials, rate limited) is a FORM-LEVEL
   banner at the top of the form (ErrorMessage), persisting until the
   next attempt: the standard error-summary pattern every serious login
   form uses. (c) Toasts are for outcomes of operations detached from a
   form the user is staring at (a fill failed, a save bounced). Never
   toast tier a or b; never banner tier c.
