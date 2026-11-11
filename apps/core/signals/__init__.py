# `signals` is the app's window onto the signals service: listeners that
# follow funding news for an account, the matches they find, and previews
# of a setup before it is saved. A person's request calls the service with
# their session's IdP access token (signals:read and signals:write); an
# account's feed, with no one present, with the account's sync key
# (SignalsSyncKey, signals:read). No routes yet.
