"""mesh-runtime — the Mesh local execution daemon.

Turns server-queued, frozen ``task_execution`` rows into auditable local
executions: activation/heartbeat/claim/renew, lease reconciliation, journaling,
redacted log relay and provider supervision. See docs/specs/features/runtime-executor.md.
"""

__version__ = "0.1.0"

PROTOCOL_VERSION = "1.0"
RUNTIME_TOKEN_PREFIX = "mesh_rt_"
TASK_TOKEN_PREFIX = "mesh_task_"
