"""The SEARCH tool family: the provider seam (vendors under
`providers/`), the family failure vocabulary (`errors`), the shared
machinery (`machinery`), and the general web-search tool. The
machinery serves every search-provider-backed tool, whichever family
package it lives in (the contacts family composes it from next
door); a tool with a vocabulary of its own gets its own package of
tool + errors the same way."""
