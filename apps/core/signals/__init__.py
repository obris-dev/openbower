# `signals` is the app's window onto the signals service: listeners that
# follow funding news for an account, the matches they find, and previews
# of a setup before it is saved. It calls the service with the session
# user's IdP access token (scopes signals:read and signals:write). No
# models of its own yet, and no routes: the client comes first.
