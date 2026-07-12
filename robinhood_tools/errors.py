class RobinhoodToolError(Exception):
    """Base error for Robinhood tool failures."""


class ConnectorUnavailable(RobinhoodToolError):
    """Raised when the backend connector is not mounted in this project."""


class AuthorizationRequired(RobinhoodToolError):
    """Raised when the user has not authorized the Robinhood connector."""


class EntitlementMissing(RobinhoodToolError):
    """Raised when connector/account entitlements do not allow the operation."""


class PolicyViolation(RobinhoodToolError):
    """Raised when a request violates local trading safety policy."""


class ConfirmationRequired(PolicyViolation):
    """Raised when a real order/cancellation lacks explicit confirmation."""
