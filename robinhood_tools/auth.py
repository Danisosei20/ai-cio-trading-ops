from __future__ import annotations

from dataclasses import dataclass, field

from .errors import AuthorizationRequired, ConnectorUnavailable, EntitlementMissing


@dataclass(frozen=True)
class AuthorizationState:
    connector_enabled: bool = True
    granted_scopes: frozenset[str] = field(default_factory=frozenset)


class ToolAuthorizer:
    def __init__(self, state: AuthorizationState):
        self.state = state

    def require(self, *scopes: str) -> None:
        if not self.state.connector_enabled:
            raise ConnectorUnavailable("Robinhood connector is not available in this project/session.")

        missing = set(scopes) - set(self.state.granted_scopes)
        if not missing:
            return

        if any(scope.endswith("_write") or scope.endswith("_review") for scope in missing):
            raise EntitlementMissing(f"Missing Robinhood entitlement or connector scope: {sorted(missing)}")
        raise AuthorizationRequired(f"Robinhood authorization is missing required scope: {sorted(missing)}")
