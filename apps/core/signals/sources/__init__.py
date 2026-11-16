"""The source kinds: what feeds a signal (a log sheet), one module per
kind, each registering itself at its own bottom (`register(HostedSource)`).
The roster is SignalsConfig.ready()'s walk of this package, so a new kind
is a new file here, never an edit to the registry or an enum."""
