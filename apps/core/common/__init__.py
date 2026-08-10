# `common` holds cross-app plumbing that is THIS service's, not the
# kernel's: shared test harnesses (testing.py) and, as later phases land,
# service-specific error contracts and seams. Framework-agnostic
# primitives (ULID field, BaseModel, env helpers, healthz) live in
# openbower_kernel.
