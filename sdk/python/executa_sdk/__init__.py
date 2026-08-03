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
from .credentials import (  # noqa: F401
    CredentialsClient,
    CredentialsError,
    METHOD_CREDENTIALS_LIST_ACCOUNTS,
    METHOD_CREDENTIALS_GET_TOKEN,
)
from .web import (  # noqa: F401
    WebClient,
    WebError,
    METHOD_WEB_SEARCH,
    METHOD_WEB_FETCH,
)
from .context import (  # noqa: F401
    InvokeContext,
    attach_invoke_context,
    bind_invoke,
    get_current_invoke_id,
)
from .progress import (  # noqa: F401
    METHOD_EXECUTA_PROGRESS,
    emit_progress,
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
    "ImageClient",
    "ImageError",
    "HostUploadClient",
    "UploadError",
    "EmbeddingsClient",
    "EmbeddingsError",
    "CredentialsClient",
    "CredentialsError",
    "WebClient",
    "WebError",
    "InvokeContext",
    "bind_invoke",
    "get_current_invoke_id",
    "attach_invoke_context",
    "emit_progress",
    "METHOD_EXECUTA_PROGRESS",
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
