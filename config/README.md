# config/

User-owned runtime configuration (gitignored; the templates/ directory
and this README are the tracked parts). `bower-admin setup` seeds each
file from its template and writes what you give it; a hand edit is
applied with `bower-admin reload`. The two files:

- `providers.toml` (from templates/providers.example.toml): the named
  inference sources agents can run, structure AND keys in this ONE
  file. Keys live INLINE (this directory is gitignored; the
  aws-credentials/npmrc norm for operator-owned config); there is no
  env mirror. Section names are the registered provider specs,
  validated at boot: a typo'd section refuses startup naming the
  section and the registered names.
- `tools.toml` (from templates/tools.example.toml): the vendors behind
  the agents' tools, wiring AND credentials in this ONE file. A
  `[tools]` section wires each tool to the vendor that serves it; a
  table per vendor holds its credentials inline. Names are validated
  at boot against the registered vendors and tools: a typo refuses
  startup naming the valid options.
