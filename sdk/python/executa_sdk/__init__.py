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
from .image import (  # noqa: F401
    ImageClient,
    ImageError,
    METHOD_IMAGE_GENERATE,
    METHOD_IMAGE_EDIT,
)
from .host_upload import (  # noqa: F401
    HostUploadClient,
    UploadError,
    METHOD_HOST_UPLOAD_FILE,
)
from .embeddings import (  # noqa: F401
    EmbeddingsClient,
    EmbeddingsError,
    METHOD_EMBEDDINGS_CREATE,
)
from .context import InvokeContext  # noqa: F401

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
    "ImageClient",
    "ImageError",
    "HostUploadClient",
    "UploadError",
    "EmbeddingsClient",
    "EmbeddingsError",
    "InvokeContext",
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
    "METHOD_IMAGE_GENERATE",
    "METHOD_IMAGE_EDIT",
    "METHOD_HOST_UPLOAD_FILE",
    "METHOD_EMBEDDINGS_CREATE",
]
