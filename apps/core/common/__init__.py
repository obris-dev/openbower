# `common` holds the foundational primitives every app builds on: the ULID
# primary-key field + BaseModel, the /v1 URL helpers, the env helpers, and
# the healthz probe. It is intended to graduate into a shared, installable
# package once there is a second consumer to justify the packaging cost;
# until then it lives here as plain app code.
