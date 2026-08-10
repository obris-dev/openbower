"""The shared Django kernel for the cloud service leaves.

The service-agnostic primitives every leaf builds on: the ULID primary-key
field + BaseModel, env parsing, the trailing-slash-optional `/v1` URL
helpers, the /healthz probe, token fingerprinting, cursor-pagination
helpers, and the DJANGO_ENV selector. One copy, both leaves depend on it
via the workspace; service-SPECIFIC infrastructure (error contracts,
authentication seams) stays in each leaf's own `common` app.

Not a Django app: nothing here has models or needs registration. Leaves
add an "openbower_kernel" logger to LOGGING so warnings surface.
"""
