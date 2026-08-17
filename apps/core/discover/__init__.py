# `discover` is the app's window onto the data service: a thin proxy that
# forwards the session user's IdP access token (scope data:read) to the
# look-alike API and returns the shared-contract response. No models of its
# own in this slice; curation/lists land later and will live beside it.
