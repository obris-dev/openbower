# config/

User-owned runtime configuration (gitignored; the templates/ directory
and this README are the tracked parts). Copy a template up one level
and edit:

- `providers.toml` (from templates/providers.example.toml): the named
  inference sources agents can run, structure AND keys in this ONE
  file. Keys live INLINE (this directory is gitignored; the
  aws-credentials/npmrc norm for operator-owned config); there is no
  env mirror.
