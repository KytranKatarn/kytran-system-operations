"""Kytran System Operations — Self-hosted server management dashboard."""
import os
import sys

__version__ = "0.1.0"

# Put this package directory on sys.path so the modules copied from
# ARCHIE's platform_v2/tools/system_operations (which import `database`,
# `rbac`, `tools.blueprint.blueprint_intelligence` as top-level modules)
# resolve to our local shims without any per-file rewrites. Keeping the
# copied files byte-identical to the platform makes future re-syncs
# trivial.
_pkg_dir = os.path.dirname(os.path.abspath(__file__))
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)
