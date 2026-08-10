"""The shared Django kernel.

The service-agnostic primitives the Django services build on: the ULID
primary-key field + BaseModel, env parsing, the trailing-slash-optional
`/v1` URL helpers, the /healthz probe, token fingerprinting,
cursor-pagination helpers, and the DJANGO_ENV selector. Consumed via the
workspace; service-SPECIFIC infrastructure (error contracts,
authentication seams) stays in each service's own apps.

Not a Django app: nothing here has models or needs registration. Leaves
add an "openbower_kernel" logger to LOGGING so warnings surface.
"""
