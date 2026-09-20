from __future__ import annotations

import os

from .security import is_loopback_host, load_or_create_api_token


def main() -> None:
    import uvicorn

    host = os.getenv("PROJECT_ASSISTANT_HOST", "127.0.0.1")
    if not is_loopback_host(host) and os.getenv("PROJECT_ASSISTANT_ALLOW_NON_LOOPBACK") != "1":
        raise RuntimeError(
            "Refusing to bind the Project Assistant API to a non-loopback interface. "
            "Use 127.0.0.1/localhost, or set PROJECT_ASSISTANT_ALLOW_NON_LOOPBACK=1 only if you have added an external security boundary."
        )
    # Create/validate the shared local API token before the server accepts traffic.
    load_or_create_api_token()
    port = int(os.getenv("PROJECT_ASSISTANT_PORT", "8000"))
    uvicorn.run(
        "project_assistant.api.app:app",
        host=host,
        port=port,
        reload=False,
        proxy_headers=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
