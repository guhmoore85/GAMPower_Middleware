"""
Custom Exception Hierarchy for PAYGO Middleware

CRITICAL: All exceptions in this module are designed to be LOUD.
They capture:
- Timestamp of occurrence
- Request ID for tracing
- Full context of the error
- Stack traces where applicable

Every exception should be logged at ERROR level with full context.
"""

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID


class BasePaygoError(Exception):
    """
    Base exception for all PAYGO middleware errors.

    All exceptions include:
    - timestamp: When the error occurred
    - request_id: For request tracing (if available)
    - context: Additional context about the error
    - error_code: Machine-readable error code

    LOUD FAILURE: This exception and all subclasses will log themselves
    when created if a logger is provided.
    """

    error_code: str = "PAYGO_ERROR"
    http_status_code: int = 500

    def __init__(
        self,
        message: str,
        *,
        request_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
        original_error: Optional[Exception] = None,
    ) -> None:
        self.message = message
        self.timestamp = datetime.now(timezone.utc)
        self.request_id = request_id
        self.context = context or {}
        self.original_error = original_error

        # Build detailed error message
        full_message = self._build_full_message()
        super().__init__(full_message)

    def _build_full_message(self) -> str:
        """Build comprehensive error message with all context."""
        parts = [
            f"[{self.error_code}] {self.message}",
            f"  Timestamp: {self.timestamp.isoformat()}",
        ]

        if self.request_id:
            parts.append(f"  Request ID: {self.request_id}")

        if self.context:
            parts.append("  Context:")
            for key, value in self.context.items():
                # Sanitize sensitive values
                if any(sensitive in key.lower() for sensitive in ["password", "secret", "key", "token"]):
                    value = "[REDACTED]"
                parts.append(f"    {key}: {value}")

        if self.original_error:
            parts.append(f"  Original Error: {type(self.original_error).__name__}: {self.original_error}")

        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert exception to dictionary for API responses.
        IMPORTANT: Sanitizes sensitive data before returning.
        """
        return {
            "error_code": self.error_code,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "request_id": self.request_id,
            "details": self._sanitize_context(),
        }

    def _sanitize_context(self) -> dict[str, Any]:
        """Remove sensitive data from context for API responses."""
        sanitized = {}
        sensitive_keys = {"password", "secret", "key", "token", "api_key", "credential"}

        for key, value in self.context.items():
            if any(s in key.lower() for s in sensitive_keys):
                sanitized[key] = "[REDACTED]"
            elif isinstance(value, (str, int, float, bool, type(None))):
                sanitized[key] = value
            elif isinstance(value, UUID):
                sanitized[key] = str(value)
            else:
                sanitized[key] = str(value)

        return sanitized


class ValidationError(BasePaygoError):
    """
    Raised when input validation fails.

    LOUD: Logs every field that failed validation with reasons.
    """

    error_code = "VALIDATION_ERROR"
    http_status_code = 400

    def __init__(
        self,
        message: str,
        *,
        field_errors: Optional[dict[str, str]] = None,
        request_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        self.field_errors = field_errors or {}

        # Add field errors to context
        context = context or {}
        if self.field_errors:
            context["field_errors"] = self.field_errors

        super().__init__(message, request_id=request_id, context=context)

    def to_dict(self) -> dict[str, Any]:
        result = super().to_dict()
        result["field_errors"] = self.field_errors
        return result


class DeviceNotFoundError(BasePaygoError):
    """
    Raised when a device cannot be found.

    LOUD: Always logs the device_id that was not found.
    """

    error_code = "DEVICE_NOT_FOUND"
    http_status_code = 404

    def __init__(
        self,
        device_id: str | UUID,
        *,
        request_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        self.device_id = str(device_id)

        context = context or {}
        context["device_id"] = self.device_id

        super().__init__(
            f"Device not found: {self.device_id}",
            request_id=request_id,
            context=context,
        )


class CustomerNotFoundError(BasePaygoError):
    """
    Raised when a customer cannot be found.

    LOUD: Always logs the customer_id that was not found.
    """

    error_code = "CUSTOMER_NOT_FOUND"
    http_status_code = 404

    def __init__(
        self,
        customer_id: str | UUID,
        *,
        request_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        self.customer_id = str(customer_id)

        context = context or {}
        context["customer_id"] = self.customer_id

        super().__init__(
            f"Customer not found: {self.customer_id}",
            request_id=request_id,
            context=context,
        )


class TransactionNotFoundError(BasePaygoError):
    """
    Raised when a transaction cannot be found.

    LOUD: Always logs the transaction_id that was not found.
    """

    error_code = "TRANSACTION_NOT_FOUND"
    http_status_code = 404

    def __init__(
        self,
        transaction_id: str | UUID,
        *,
        request_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        self.transaction_id = str(transaction_id)

        context = context or {}
        context["transaction_id"] = self.transaction_id

        super().__init__(
            f"Transaction not found: {self.transaction_id}",
            request_id=request_id,
            context=context,
        )


class InvalidTokenError(BasePaygoError):
    """
    Raised when an OpenPAYGO token is invalid.

    LOUD: Logs token validation failure details (but not the token itself).
    """

    error_code = "INVALID_TOKEN"
    http_status_code = 400

    def __init__(
        self,
        reason: str,
        *,
        device_id: Optional[str | UUID] = None,
        request_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        self.reason = reason
        self.device_id = str(device_id) if device_id else None

        context = context or {}
        context["reason"] = reason
        if self.device_id:
            context["device_id"] = self.device_id

        super().__init__(
            f"Invalid token: {reason}",
            request_id=request_id,
            context=context,
        )


class TokenGenerationError(BasePaygoError):
    """
    Raised when token generation fails.

    LOUD: Logs all details about the generation failure.
    """

    error_code = "TOKEN_GENERATION_ERROR"
    http_status_code = 500

    def __init__(
        self,
        device_id: str | UUID,
        reason: str,
        *,
        request_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
        original_error: Optional[Exception] = None,
    ) -> None:
        self.device_id = str(device_id)
        self.reason = reason

        context = context or {}
        context["device_id"] = self.device_id
        context["reason"] = reason

        super().__init__(
            f"Failed to generate token for device {self.device_id}: {reason}",
            request_id=request_id,
            context=context,
            original_error=original_error,
        )


class PaymentProviderError(BasePaygoError):
    """
    Raised when a payment provider operation fails.

    LOUD: Logs provider name, operation, and all relevant details.
    Sanitizes API keys and credentials.
    """

    error_code = "PAYMENT_PROVIDER_ERROR"
    http_status_code = 502  # Bad Gateway - upstream service failed

    def __init__(
        self,
        provider: str,
        operation: str,
        *,
        transaction_id: Optional[str] = None,
        status_code: Optional[int] = None,
        provider_error_code: Optional[str] = None,
        provider_message: Optional[str] = None,
        request_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
        original_error: Optional[Exception] = None,
    ) -> None:
        self.provider = provider
        self.operation = operation
        self.transaction_id = transaction_id
        self.status_code = status_code
        self.provider_error_code = provider_error_code
        self.provider_message = provider_message

        context = context or {}
        context["provider"] = provider
        context["operation"] = operation
        if transaction_id:
            context["transaction_id"] = transaction_id
        if status_code:
            context["status_code"] = status_code
        if provider_error_code:
            context["provider_error_code"] = provider_error_code
        if provider_message:
            context["provider_message"] = provider_message

        message = f"Payment provider '{provider}' failed during '{operation}'"
        if provider_message:
            message += f": {provider_message}"

        super().__init__(
            message,
            request_id=request_id,
            context=context,
            original_error=original_error,
        )


class TriggerEvaluationError(BasePaygoError):
    """
    Raised when trigger evaluation fails.

    LOUD: Logs device_id, trigger_type, and evaluation details.
    """

    error_code = "TRIGGER_EVALUATION_ERROR"
    http_status_code = 500

    def __init__(
        self,
        device_id: str | UUID,
        trigger_type: str,
        reason: str,
        *,
        trigger_id: Optional[str | UUID] = None,
        request_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
        original_error: Optional[Exception] = None,
    ) -> None:
        self.device_id = str(device_id)
        self.trigger_type = trigger_type
        self.trigger_id = str(trigger_id) if trigger_id else None
        self.reason = reason

        context = context or {}
        context["device_id"] = self.device_id
        context["trigger_type"] = trigger_type
        if self.trigger_id:
            context["trigger_id"] = self.trigger_id
        context["reason"] = reason

        super().__init__(
            f"Trigger evaluation failed for device {self.device_id} ({trigger_type}): {reason}",
            request_id=request_id,
            context=context,
            original_error=original_error,
        )


class DatabaseError(BasePaygoError):
    """
    Raised when a database operation fails.

    LOUD: Logs operation, table, and error details.
    """

    error_code = "DATABASE_ERROR"
    http_status_code = 500

    def __init__(
        self,
        operation: str,
        *,
        table: Optional[str] = None,
        query_params: Optional[dict[str, Any]] = None,
        request_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
        original_error: Optional[Exception] = None,
    ) -> None:
        self.operation = operation
        self.table = table

        context = context or {}
        context["operation"] = operation
        if table:
            context["table"] = table
        # Don't include query params in context - might have sensitive data

        message = f"Database operation failed: {operation}"
        if table:
            message += f" on table '{table}'"

        super().__init__(
            message,
            request_id=request_id,
            context=context,
            original_error=original_error,
        )


class EncryptionError(BasePaygoError):
    """
    Raised when encryption/decryption fails.

    LOUD: Logs operation failure but NEVER logs the data being encrypted.
    """

    error_code = "ENCRYPTION_ERROR"
    http_status_code = 500

    def __init__(
        self,
        operation: str,  # "encrypt" or "decrypt"
        reason: str,
        *,
        request_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
        original_error: Optional[Exception] = None,
    ) -> None:
        self.operation = operation
        self.reason = reason

        context = context or {}
        context["operation"] = operation
        context["reason"] = reason

        super().__init__(
            f"Encryption operation '{operation}' failed: {reason}",
            request_id=request_id,
            context=context,
            original_error=original_error,
        )


class AuthenticationError(BasePaygoError):
    """
    Raised when authentication fails.

    LOUD: Logs authentication failure reason but not credentials.
    """

    error_code = "AUTHENTICATION_ERROR"
    http_status_code = 401

    def __init__(
        self,
        reason: str,
        *,
        request_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        self.reason = reason

        context = context or {}
        context["reason"] = reason

        super().__init__(
            f"Authentication failed: {reason}",
            request_id=request_id,
            context=context,
        )


class AuthorizationError(BasePaygoError):
    """
    Raised when authorization fails.

    LOUD: Logs what resource was accessed and why access was denied.
    """

    error_code = "AUTHORIZATION_ERROR"
    http_status_code = 403

    def __init__(
        self,
        resource: str,
        action: str,
        *,
        request_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        self.resource = resource
        self.action = action

        context = context or {}
        context["resource"] = resource
        context["action"] = action

        super().__init__(
            f"Access denied: cannot {action} {resource}",
            request_id=request_id,
            context=context,
        )


class WebhookVerificationError(BasePaygoError):
    """
    Raised when webhook signature verification fails.

    LOUD: Logs provider and verification failure details.
    """

    error_code = "WEBHOOK_VERIFICATION_ERROR"
    http_status_code = 401

    def __init__(
        self,
        provider: str,
        reason: str,
        *,
        request_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        self.provider = provider
        self.reason = reason

        context = context or {}
        context["provider"] = provider
        context["reason"] = reason

        super().__init__(
            f"Webhook verification failed for {provider}: {reason}",
            request_id=request_id,
            context=context,
        )


class RateLimitError(BasePaygoError):
    """
    Raised when rate limit is exceeded.

    LOUD: Logs rate limit details.
    """

    error_code = "RATE_LIMIT_ERROR"
    http_status_code = 429

    def __init__(
        self,
        limit: int,
        window_seconds: int,
        *,
        retry_after: Optional[int] = None,
        request_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.retry_after = retry_after

        context = context or {}
        context["limit"] = limit
        context["window_seconds"] = window_seconds
        if retry_after:
            context["retry_after"] = retry_after

        super().__init__(
            f"Rate limit exceeded: {limit} requests per {window_seconds} seconds",
            request_id=request_id,
            context=context,
        )


class ConfigurationError(BasePaygoError):
    """
    Raised when configuration is invalid.

    LOUD: Logs which configuration is invalid and why.
    Used during startup validation.
    """

    error_code = "CONFIGURATION_ERROR"
    http_status_code = 500

    def __init__(
        self,
        config_name: str,
        reason: str,
        *,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        self.config_name = config_name
        self.reason = reason

        context = context or {}
        context["config_name"] = config_name
        context["reason"] = reason

        super().__init__(
            f"Configuration error for '{config_name}': {reason}",
            context=context,
        )


class ServiceUnavailableError(BasePaygoError):
    """
    Raised when a required service is unavailable.

    LOUD: Logs which service is unavailable and diagnostic info.
    """

    error_code = "SERVICE_UNAVAILABLE"
    http_status_code = 503

    def __init__(
        self,
        service: str,
        reason: str,
        *,
        request_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
        original_error: Optional[Exception] = None,
    ) -> None:
        self.service = service
        self.reason = reason

        context = context or {}
        context["service"] = service
        context["reason"] = reason

        super().__init__(
            f"Service '{service}' is unavailable: {reason}",
            request_id=request_id,
            context=context,
            original_error=original_error,
        )


class IdempotencyError(BasePaygoError):
    """
    Raised when idempotency check fails.

    LOUD: Logs the duplicate request detection.
    """

    error_code = "IDEMPOTENCY_ERROR"
    http_status_code = 409

    def __init__(
        self,
        idempotency_key: str,
        *,
        existing_status: Optional[str] = None,
        request_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        self.idempotency_key = idempotency_key
        self.existing_status = existing_status

        context = context or {}
        context["idempotency_key"] = idempotency_key
        if existing_status:
            context["existing_status"] = existing_status

        super().__init__(
            f"Duplicate request with idempotency key: {idempotency_key}",
            request_id=request_id,
            context=context,
        )


class DeviceSuspendedError(BasePaygoError):
    """
    Raised when trying to operate on a suspended device.

    LOUD: Logs the device ID and operation attempted.
    """

    error_code = "DEVICE_SUSPENDED"
    http_status_code = 403

    def __init__(
        self,
        device_id: str | UUID,
        operation: str,
        *,
        request_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        self.device_id = str(device_id)
        self.operation = operation

        context = context or {}
        context["device_id"] = self.device_id
        context["operation"] = operation

        super().__init__(
            f"Cannot {operation} suspended device: {self.device_id}",
            request_id=request_id,
            context=context,
        )
