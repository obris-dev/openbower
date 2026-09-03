"""The search family's PROVIDERS: one module per provider, each
declaring a SPEC and registering it at its own bottom, the same shape
a tool takes. Deliberately INERT (no imports): the roster lives in
AgentsConfig.ready(), so importing one piece of the package never
drags the rest."""
