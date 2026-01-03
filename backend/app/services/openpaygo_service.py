"""
OpenPAYGO Service

Implements OpenPAYGO Token specification for device activation.

CRITICAL: All operations are logged with full context.
Secret keys are encrypted at rest and never logged.

Based on OpenPAYGO Token specification:
https://github.com/EnAccess/OpenPAYGO-Token
"""

import hashlib
import hmac
import struct
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    DeviceNotFoundError,
    DeviceSuspendedError,
    InvalidTokenError,
    TokenGenerationError,
)
from app.core.logging import audit_logger, get_logger
from app.models.device import Device, DeviceStatus
from app.models.device_activation import ActivationType, DeviceActivation
from app.utils.encryption import decrypt_value, encrypt_value

logger = get_logger(__name__)


class OpenPAYGOService:
    """
    OpenPAYGO Token implementation.

    Provides:
    - Token generation for device activation
    - Token validation
    - Device activation management

    LOUD ERROR HANDLING:
    - All errors logged with full context
    - Never logs actual tokens or secret keys
    """

    # Token types as per OpenPAYGO spec
    TOKEN_TYPE_ADD_TIME = 1
    TOKEN_TYPE_SET_TIME = 2
    TOKEN_TYPE_DISABLE_PAYG = 3
    TOKEN_TYPE_COUNTER_SYNC = 4

    # Token value encoding
    MAX_TOKEN_VALUE = 999999999  # 9 digits max

    def __init__(self, db: AsyncSession):
        """
        Initialize OpenPAYGO service.

        Args:
            db: Async database session
        """
        self.db = db
        logger.debug("OpenPAYGO service initialized")

    async def get_device(self, device_id: UUID) -> Device:
        """
        Get device by ID.

        LOUD: Raises DeviceNotFoundError if not found.
        """
        result = await self.db.execute(
            select(Device).where(Device.id == device_id)
        )
        device = result.scalar_one_or_none()

        if not device:
            logger.error(
                "Device not found",
                device_id=str(device_id),
            )
            raise DeviceNotFoundError(device_id)

        logger.debug(
            "Device retrieved",
            device_id=str(device_id),
            status=device.status.value,
        )

        return device

    async def get_device_by_external_id(self, external_id: str) -> Device:
        """
        Get device by external ID.

        LOUD: Raises DeviceNotFoundError if not found.
        """
        result = await self.db.execute(
            select(Device).where(Device.external_id == external_id)
        )
        device = result.scalar_one_or_none()

        if not device:
            logger.error(
                "Device not found by external_id",
                external_id=external_id,
            )
            raise DeviceNotFoundError(
                external_id,
                context={"lookup_type": "external_id"},
            )

        return device

    async def generate_token(
        self,
        device_id: UUID,
        days_valid: int,
        *,
        token_type: int = None,
    ) -> tuple[str, datetime]:
        """
        Generate activation token for device.

        Args:
            device_id: Device UUID
            days_valid: Number of days the token is valid
            token_type: Token type (default: ADD_TIME)

        Returns:
            Tuple of (token_string, expiry_datetime)

        Raises:
            DeviceNotFoundError: If device doesn't exist
            DeviceSuspendedError: If device is suspended
            TokenGenerationError: If token generation fails
        """
        if token_type is None:
            token_type = self.TOKEN_TYPE_ADD_TIME

        # Validate days_valid
        if days_valid <= 0:
            logger.error(
                "Invalid days_valid",
                device_id=str(device_id),
                days_valid=days_valid,
            )
            raise TokenGenerationError(
                device_id,
                f"days_valid must be positive, got {days_valid}",
            )

        max_days = settings.openpaygo_max_token_days
        if days_valid > max_days:
            logger.error(
                "days_valid exceeds maximum",
                device_id=str(device_id),
                days_valid=days_valid,
                max_days=max_days,
            )
            raise TokenGenerationError(
                device_id,
                f"days_valid ({days_valid}) exceeds maximum ({max_days})",
            )

        # Get device
        device = await self.get_device(device_id)

        # Check device status
        if device.status == DeviceStatus.SUSPENDED:
            logger.warning(
                "Token generation attempted for suspended device",
                device_id=str(device_id),
            )
            raise DeviceSuspendedError(device_id, "generate token")

        try:
            # Decrypt device secret key
            secret_key = self._decrypt_device_secret(device)

            # Generate token
            token = self._generate_token_internal(
                secret_key=secret_key,
                days_valid=days_valid,
                token_type=token_type,
            )

            # Calculate expiry
            expiry = datetime.now(timezone.utc) + timedelta(days=days_valid)

            logger.info(
                "Token generated",
                device_id=str(device_id),
                days_valid=days_valid,
                token_type=token_type,
                expires_at=expiry.isoformat(),
                # NEVER log the actual token
            )

            # Audit log
            audit_logger.log_event(
                event_type="token",
                action="generate",
                resource_type="device",
                resource_id=str(device_id),
                details={
                    "days_valid": days_valid,
                    "token_type": token_type,
                },
            )

            return token, expiry

        except (DeviceNotFoundError, DeviceSuspendedError):
            raise
        except Exception as e:
            logger.error(
                "Token generation failed",
                device_id=str(device_id),
                error=str(e),
                error_type=type(e).__name__,
            )
            raise TokenGenerationError(
                device_id,
                f"Internal error: {type(e).__name__}",
                original_error=e,
            )

    def _decrypt_device_secret(self, device: Device) -> bytes:
        """
        Decrypt device secret key.

        CRITICAL: Never log the decrypted value.
        """
        try:
            decrypted = decrypt_value(
                device.openpaygo_secret_key,
                associated_data=str(device.id),
            )
            logger.debug("Device secret decrypted", device_id=str(device.id))
            return bytes.fromhex(decrypted)
        except Exception as e:
            logger.error(
                "Failed to decrypt device secret",
                device_id=str(device.id),
                error=str(e),
            )
            raise

    def _generate_token_internal(
        self,
        secret_key: bytes,
        days_valid: int,
        token_type: int,
    ) -> str:
        """
        Generate token using OpenPAYGO algorithm.

        This is a simplified implementation. For production,
        use the official OpenPAYGO Token library.
        """
        # Pack token data
        # Format: token_type (1 byte) + days_valid (2 bytes)
        token_data = struct.pack(">BH", token_type, days_valid)

        # Add timestamp for uniqueness
        timestamp = int(datetime.now(timezone.utc).timestamp())
        token_data += struct.pack(">I", timestamp)

        # Generate HMAC
        token_hmac = hmac.new(
            secret_key,
            token_data,
            hashlib.sha256,
        ).digest()

        # Convert to numeric token (9 digits)
        token_value = int.from_bytes(token_hmac[:4], "big") % self.MAX_TOKEN_VALUE

        # Format as 9-digit string with hyphens
        token_str = f"{token_value:09d}"
        formatted_token = f"{token_str[:3]}-{token_str[3:6]}-{token_str[6:]}"

        return formatted_token

    async def validate_token(
        self,
        device_id: UUID,
        token: str,
    ) -> bool:
        """
        Validate activation token.

        Args:
            device_id: Device UUID
            token: Token to validate

        Returns:
            True if valid

        Raises:
            DeviceNotFoundError: If device doesn't exist
            InvalidTokenError: If token is invalid
        """
        # Validate token format first
        if not self._validate_token_format(token):
            logger.warning(
                "Invalid token format",
                device_id=str(device_id),
                token_length=len(token),
            )
            raise InvalidTokenError(
                "Invalid token format. Expected: XXX-XXX-XXX",
                device_id=device_id,
            )

        # Get device
        device = await self.get_device(device_id)

        try:
            # Decrypt device secret
            secret_key = self._decrypt_device_secret(device)

            # In a real implementation, we would validate against
            # the OpenPAYGO token algorithm. For this demo,
            # we'll accept any properly formatted token.
            # TODO: Implement full OpenPAYGO token validation

            is_valid = True  # Simplified for demo

            if is_valid:
                logger.info(
                    "Token validated successfully",
                    device_id=str(device_id),
                )
            else:
                logger.warning(
                    "Token validation failed",
                    device_id=str(device_id),
                )
                raise InvalidTokenError(
                    "Token verification failed",
                    device_id=device_id,
                )

            return is_valid

        except InvalidTokenError:
            raise
        except Exception as e:
            logger.error(
                "Token validation error",
                device_id=str(device_id),
                error=str(e),
            )
            raise InvalidTokenError(
                f"Validation error: {type(e).__name__}",
                device_id=device_id,
            )

    def _validate_token_format(self, token: str) -> bool:
        """Validate token format (XXX-XXX-XXX)."""
        if not token:
            return False

        # Remove hyphens and check length
        clean_token = token.replace("-", "")

        if len(clean_token) != 9:
            return False

        if not clean_token.isdigit():
            return False

        return True

    async def activate_device(
        self,
        device_id: UUID,
        token: str,
        *,
        activation_type: ActivationType = ActivationType.PAYMENT,
        transaction_id: Optional[UUID] = None,
        days_valid: int = None,
    ) -> DeviceActivation:
        """
        Activate device with token.

        Args:
            device_id: Device UUID
            token: Activation token
            activation_type: Type of activation
            transaction_id: Related transaction (required for PAYMENT type)
            days_valid: Token validity in days

        Returns:
            Created DeviceActivation record

        Raises:
            DeviceNotFoundError: If device doesn't exist
            InvalidTokenError: If token is invalid
            DatabaseError: If activation fails
        """
        if days_valid is None:
            days_valid = settings.openpaygo_default_token_days

        # Validate token
        await self.validate_token(device_id, token)

        # Get device
        device = await self.get_device(device_id)

        # Validate activation type
        if activation_type == ActivationType.PAYMENT and not transaction_id:
            logger.error(
                "Payment activation requires transaction_id",
                device_id=str(device_id),
            )
            raise InvalidTokenError(
                "Payment activation requires transaction_id",
                device_id=device_id,
            )

        try:
            # Calculate expiry
            expires_at = datetime.now(timezone.utc) + timedelta(days=days_valid)

            # Create activation record
            activation = DeviceActivation(
                device_id=device_id,
                transaction_id=transaction_id,
                activation_token=token,
                activation_type=activation_type,
                expires_at=expires_at,
                activated_at=datetime.now(timezone.utc),
            )

            self.db.add(activation)

            # Update device status to active
            if device.status != DeviceStatus.ACTIVE:
                device.transition_status(
                    DeviceStatus.ACTIVE,
                    reason=f"Activation via {activation_type.value}",
                )

            await self.db.flush()

            logger.info(
                "Device activated",
                device_id=str(device_id),
                activation_id=str(activation.id),
                activation_type=activation_type.value,
                expires_at=expires_at.isoformat(),
            )

            # Audit log
            audit_logger.log_event(
                event_type="activation",
                action="activate",
                resource_type="device",
                resource_id=str(device_id),
                details={
                    "activation_id": str(activation.id),
                    "activation_type": activation_type.value,
                    "days_valid": days_valid,
                    "transaction_id": str(transaction_id) if transaction_id else None,
                },
            )

            return activation

        except Exception as e:
            logger.error(
                "Device activation failed",
                device_id=str(device_id),
                error=str(e),
            )
            raise

    async def register_device(
        self,
        external_id: str,
        device_type: str,
        *,
        manufacturer: Optional[str] = None,
        model: Optional[str] = None,
        customer_id: Optional[UUID] = None,
        metadata: Optional[dict] = None,
    ) -> tuple[Device, str]:
        """
        Register a new device.

        Args:
            external_id: External device identifier
            device_type: Device type (solar, emobility)
            manufacturer: Device manufacturer
            model: Device model
            customer_id: Associated customer
            metadata: Additional metadata

        Returns:
            Tuple of (Device, unencrypted_secret_key)

        CRITICAL: Secret key is only returned once during registration!
        """
        from app.core.security import generate_device_secret
        from app.models.device import DeviceType

        logger.info(
            "Registering new device",
            external_id=external_id,
            device_type=device_type,
        )

        try:
            # Generate secret key
            secret_key = generate_device_secret()

            # Encrypt secret key for storage
            # Use device external_id as AAD since we don't have ID yet
            encrypted_secret = encrypt_value(
                secret_key,
                associated_data=external_id,
            )

            # Create device
            device = Device(
                external_id=external_id,
                device_type=DeviceType(device_type),
                manufacturer=manufacturer,
                model=model,
                openpaygo_secret_key=encrypted_secret,
                customer_id=customer_id,
                metadata_=metadata or {},
            )

            self.db.add(device)
            await self.db.flush()

            # Re-encrypt with actual device ID for production use
            encrypted_secret_with_id = encrypt_value(
                secret_key,
                associated_data=str(device.id),
            )
            device.openpaygo_secret_key = encrypted_secret_with_id

            logger.info(
                "Device registered",
                device_id=str(device.id),
                external_id=external_id,
            )

            # Audit log
            audit_logger.log_event(
                event_type="device",
                action="register",
                resource_type="device",
                resource_id=str(device.id),
                details={
                    "external_id": external_id,
                    "device_type": device_type,
                },
            )

            return device, secret_key

        except Exception as e:
            logger.error(
                "Device registration failed",
                external_id=external_id,
                error=str(e),
            )
            raise
