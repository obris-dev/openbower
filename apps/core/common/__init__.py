# `common` holds cross-app plumbing that is THIS service's, not the
# kernel's (shared test harnesses; service-specific contracts). Framework
# -agnostic primitives (ULID field, BaseModel, env helpers, healthz) live
# in openbower_kernel.
