"""Authorization (M11.1): role-based access control.

:mod:`azure_finops.authz.rbac` provides the ``require_permission`` FastAPI dependency
that guards mutating endpoints, the default role catalogue, and the seeding + check
primitives. RBAC is gated by ``RBAC_ENABLED`` (off by default).
"""
