"""Anna Executa SDK — Python helpers.

This package exposes:

* ``executa_sdk.sampling`` — :class:`SamplingClient` for issuing reverse
  ``sampling/createMessage`` JSON-RPC requests to the host Agent.
* ``executa_sdk.storage`` — :class:`StorageClient` and
  :class:`FilesClient` for accessing **Anna Persistent Storage** (KV +
  object) via reverse RPC; default 5GB-per-user quota, three scopes
  (user / app / tool).
"""

from .sampling import (  # noqa: F401
    SamplingClient,
    SamplingError,
    PROTOCOL_VERSION_V1,
    PROTOCOL_VERSION_V2,
    METHOD_INITIALIZE,
    METHOD_SAMPLING_CREATE_MESSAGE,
)
from .storage import (  # noqa: F401
    StorageClient,
    FilesClient,
    StorageError,
    make_response_router,
)
from .agent import (  # noqa: F401
    AgentSession,
    AgentSessionClient,
    AgentError,
    METHOD_AGENT_SESSION_CREATE,
    METHOD_AGENT_SESSION_RUN,
    METHOD_AGENT_SESSION_CANCEL,
    METHOD_AGENT_SESSION_HISTORY,
    METHOD_AGENT_SESSION_DELETE,
    METHOD_AGENT_COMPLETE,
)

__all__ = [
    "SamplingClient",
    "SamplingError",
    "StorageClient",
    "FilesClient",
    "StorageError",
    "make_response_router",
    "AgentSession",
    "AgentSessionClient",
    "AgentError",
    "PROTOCOL_VERSION_V1",
    "PROTOCOL_VERSION_V2",
    "METHOD_INITIALIZE",
    "METHOD_SAMPLING_CREATE_MESSAGE",
    "METHOD_AGENT_SESSION_CREATE",
    "METHOD_AGENT_SESSION_RUN",
    "METHOD_AGENT_SESSION_CANCEL",
    "METHOD_AGENT_SESSION_HISTORY",
    "METHOD_AGENT_SESSION_DELETE",
    "METHOD_AGENT_COMPLETE",
]
