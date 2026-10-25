"""
API key authentication dependency.

Why a single API key and not JWT:
  Single-operator edge node on a physically isolated LAN. JWT adds session management
  complexity (refresh, revocation, clock skew) with no security benefit in this threat
  model. See architecture-design.md §5.1 and decision-log.md for full rationale.

The /health endpoint is exempt (no auth required — monitoring tools need it).
The /stream endpoint uses ?token= query param because the EventSource browser API
does not support custom headers.
"""

import os
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# auto_error=False: lets us return a structured 401 instead of FastAPI's default
# "Not authenticated" response from HTTPBearer.
_bearer = HTTPBearer(auto_error=False)


async def require_api_key(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> None:
    """Inject into any route that requires authentication."""
    api_key = os.environ.get("API_KEY", "")
    if not credentials or credentials.credentials != api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
