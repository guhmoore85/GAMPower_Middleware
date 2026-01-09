"""
OpenPAYGO Token Service

Implements OpenPAYGO Extended Token v2 generation algorithm.

Reference: https://github.com/EnAccess/OpenPAYGO-Token

CRITICAL:
- All operations are logged with full context
- Secret keys are NEVER logged
- Token values are encrypted at rest
"""

import hashlib
import hmac
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    DeviceNotFoundError,
    DeviceSuspendedError,
    InvalidTokenError,
    TokenExpiredError,
    TokenGenerationError,
    TransactionNotFoundError,
)
from app.core.logging import audit_logger, get_logger
from app.models.device import Device, DeviceStatus
from app.models.device_token import DeviceToken
from app.models.transaction import Transaction, TransactionStatus
from app.utils.encryption import decrypt_value, encrypt_value

logger = get_logger(__name__)


# OpenPAYGO Token Types
class TokenType:
    """OpenPAYGO token types as per specification."""
    ADD_TIME = 1       # Add days to current credit
    SET_TIME = 2       # Set days to specific value
    DISABLE_PAYG = 3   # Disable PAYG, unlock permanently
    COUNTER_SYNC = 4   # Sync counter without adding time


@dataclass
class TokenGenerationResult:
    """Result of token generation."""
    token: str
    token_id: UUID
    device_id: UUID
    expires_at: datetime
    days_added: int
    counter_value: int
    token_type: int
    transaction_id: Optional[UUID] = None


@dataclass
class TokenValidationResult:
    """Result of token validation."""
    is_valid: bool
    token_id: Optional[UUID] = None
    device_id: Optional[UUID] = None
    days_added: int = 0
    expires_at: Optional[datetime] = None
    error_message: Optional[str] = None
    was_already_used: bool = False


class OpenPAYGOTokenService:
    """
    OpenPAYGO Extended Token v2 implementation.

    This service implements the OpenPAYGO Token specification for
    generating and validating device activation tokens.

    Key features:
    - Token generation from transaction amount
    - Secure token validation
    - Counter-based replay protection
    - Full audit trail

    LOUD ERROR HANDLING:
    - All errors logged with full context
    - Never logs actual tokens or secret keys
    """

    # OpenPAYGO v2 constants
    TOKEN_VALUE_MASK = 0x7FFFFFFF  # 31 bits for token value
    MAX_TOKEN_VALUE = 999999999    # 9 digits max for display
    MAX_COUNTER_VALUE = 0xFFFF     # 16-bit counter

    # Pricing configuration (can be overridden per device)
    DEFAULT_PRICE_PER_DAY = Decimal("1.00")  # $1 = 1 day

    def __init__(self, db: AsyncSession):
        """Initialize token service with database session."""
        self.db = db
        logger.debug("OpenPAYGO Token Service initialized")

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    async def generate_token_from_transaction(
        self,
        device_id: UUID,
        transaction_id: UUID,
        *,
        price_per_day: Optional[Decimal] = None,
    ) -> TokenGenerationResult:
        """
        Generate token based on transaction amount.

        Calculates days from transaction amount using price_per_day.
        E.g., $10 transaction with $1/day = 10 days.

        Args:
            device_id: Device UUID
            transaction_id: Transaction UUID
            price_per_day: Price per day of access (default: $1.00)

        Returns:
            TokenGenerationResult with token details

        Raises:
            DeviceNotFoundError: Device doesn't exist
            TransactionNotFoundError: Transaction doesn't exist
            DeviceSuspendedError: Device is suspended
            TokenGenerationError: Token generation failed
        """
        logger.info(
            "Generating token from transaction",
            device_id=str(device_id),
            transaction_id=str(transaction_id),
        )

        # Get and validate device
        device = await self._get_device(device_id)
        self._validate_device_status(device)

        # Get and validate transaction
        transaction = await self._get_transaction(transaction_id)
        self._validate_transaction(transaction, device_id)

        # Calculate days from amount
        price = price_per_day or self.DEFAULT_PRICE_PER_DAY
        days_valid = self._calculate_days_from_amount(transaction.amount, price)

        logger.info(
            "Calculated days from transaction",
            device_id=str(device_id),
            transaction_id=str(transaction_id),
            amount=float(transaction.amount),
            currency=transaction.currency,
            price_per_day=float(price),
            days_calculated=days_valid,
        )

        # Generate the token
        return await self._generate_token(
            device=device,
            days_valid=days_valid,
            token_type=TokenType.ADD_TIME,
            transaction_id=transaction_id,
        )

    async def generate_token(
        self,
        device_id: UUID,
        days_valid: int,
        *,
        token_type: int = TokenType.ADD_TIME,
        transaction_id: Optional[UUID] = None,
    ) -> TokenGenerationResult:
        """
        Generate activation token for device.

        Args:
            device_id: Device UUID
            days_valid: Number of days the token grants
            token_type: Type of token (default: ADD_TIME)
            transaction_id: Related transaction (optional)

        Returns:
            TokenGenerationResult with token details

        Raises:
            DeviceNotFoundError: Device doesn't exist
            DeviceSuspendedError: Device is suspended
            TokenGenerationError: Token generation failed
        """
        logger.info(
            "Generating token",
            device_id=str(device_id),
            days_valid=days_valid,
            token_type=token_type,
        )

        # Validate days_valid
        if days_valid <= 0:
            raise TokenGenerationError(
                device_id,
                f"days_valid must be positive, got {days_valid}",
            )

        max_days = settings.openpaygo_max_token_days
        if days_valid > max_days:
            raise TokenGenerationError(
                device_id,
                f"days_valid ({days_valid}) exceeds maximum ({max_days})",
            )

        # Get and validate device
        device = await self._get_device(device_id)
        self._validate_device_status(device)

        return await self._generate_token(
            device=device,
            days_valid=days_valid,
            token_type=token_type,
            transaction_id=transaction_id,
        )

    async def validate_token(
        self,
        device_id: UUID,
        token: str,
        *,
        mark_as_used: bool = True,
    ) -> TokenValidationResult:
        """
        Validate and optionally consume a token.

        Args:
            device_id: Device UUID
            token: Token string (XXX-XXX-XXX format)
            mark_as_used: Whether to mark token as used if valid

        Returns:
            TokenValidationResult with validation details

        Raises:
            DeviceNotFoundError: Device doesn't exist
            InvalidTokenError: Token is invalid or malformed
            TokenExpiredError: Token has expired
        """
        logger.info(
            "Validating token",
            device_id=str(device_id),
            token_format_valid=self._validate_token_format(token),
        )

        # Validate format
        if not self._validate_token_format(token):
            logger.warning(
                "Invalid token format",
                device_id=str(device_id),
            )
            raise InvalidTokenError(
                "Invalid token format. Expected: XXX-XXX-XXX",
                device_id=device_id,
            )

        # Get device
        device = await self._get_device(device_id)

        # Normalize token (remove hyphens)
        clean_token = token.replace("-", "")

        # Look up token in database
        result = await self.db.execute(
            select(DeviceToken).where(
                and_(
                    DeviceToken.device_id == device_id,
                    DeviceToken.is_revoked == False,
                )
            ).order_by(DeviceToken.generated_at.desc())
        )
        stored_tokens = result.scalars().all()

        # Try to find matching token
        for stored_token in stored_tokens:
            try:
                decrypted_value = decrypt_value(
                    stored_token.token_value,
                    associated_data=str(device_id),
                )
                decrypted_clean = decrypted_value.replace("-", "")

                if decrypted_clean == clean_token:
                    # Found matching token
                    if stored_token.is_used:
                        logger.warning(
                            "Token already used",
                            device_id=str(device_id),
                            token_id=str(stored_token.id),
                        )
                        return TokenValidationResult(
                            is_valid=False,
                            token_id=stored_token.id,
                            device_id=device_id,
                            error_message="Token has already been used",
                            was_already_used=True,
                        )

                    if stored_token.is_expired():
                        logger.warning(
                            "Token expired",
                            device_id=str(device_id),
                            token_id=str(stored_token.id),
                            expires_at=stored_token.expires_at.isoformat(),
                        )
                        raise TokenExpiredError(
                            device_id=device_id,
                            token_id=stored_token.id,
                            expires_at=stored_token.expires_at,
                        )

                    # Token is valid!
                    if mark_as_used:
                        stored_token.mark_as_used()
                        await self.db.flush()

                        logger.info(
                            "Token validated and marked as used",
                            device_id=str(device_id),
                            token_id=str(stored_token.id),
                            days_added=stored_token.days_added,
                        )

                        # Audit log
                        audit_logger.log_event(
                            event_type="token",
                            action="validate",
                            resource_type="device_token",
                            resource_id=str(stored_token.id),
                            details={
                                "device_id": str(device_id),
                                "days_added": stored_token.days_added,
                                "marked_as_used": True,
                            },
                        )

                    return TokenValidationResult(
                        is_valid=True,
                        token_id=stored_token.id,
                        device_id=device_id,
                        days_added=stored_token.days_added,
                        expires_at=stored_token.expires_at,
                    )

            except Exception as e:
                # Continue to next token on decryption error
                logger.debug(
                    "Token decryption failed, trying next",
                    error=str(e),
                )
                continue

        # Also validate against OpenPAYGO algorithm for device-generated validation
        try:
            is_valid = await self._validate_token_cryptographically(device, token)
            if is_valid:
                logger.info(
                    "Token validated cryptographically",
                    device_id=str(device_id),
                )
                return TokenValidationResult(
                    is_valid=True,
                    device_id=device_id,
                    days_added=0,  # Unknown without stored token
                )
        except Exception as e:
            logger.debug(
                "Cryptographic validation failed",
                device_id=str(device_id),
                error=str(e),
            )

        # Token not found or invalid
        logger.warning(
            "Token validation failed - no matching token",
            device_id=str(device_id),
        )
        raise InvalidTokenError(
            "Token not found or invalid",
            device_id=device_id,
        )

    async def get_device_tokens(
        self,
        device_id: UUID,
        *,
        include_used: bool = False,
        include_expired: bool = False,
        limit: int = 50,
    ) -> list[DeviceToken]:
        """
        Get tokens for a device.

        Args:
            device_id: Device UUID
            include_used: Include used tokens
            include_expired: Include expired tokens
            limit: Maximum number of tokens to return

        Returns:
            List of DeviceToken objects
        """
        query = select(DeviceToken).where(
            DeviceToken.device_id == device_id,
            DeviceToken.is_revoked == False,
        )

        if not include_used:
            query = query.where(DeviceToken.is_used == False)

        if not include_expired:
            query = query.where(DeviceToken.expires_at > datetime.now(timezone.utc))

        query = query.order_by(DeviceToken.generated_at.desc()).limit(limit)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def revoke_token(
        self,
        token_id: UUID,
        reason: str,
    ) -> DeviceToken:
        """
        Revoke a token.

        Args:
            token_id: Token UUID
            reason: Reason for revocation

        Returns:
            Updated DeviceToken

        Raises:
            InvalidTokenError: Token not found
        """
        result = await self.db.execute(
            select(DeviceToken).where(DeviceToken.id == token_id)
        )
        token = result.scalar_one_or_none()

        if not token:
            logger.error("Token not found for revocation", token_id=str(token_id))
            raise InvalidTokenError(
                f"Token {token_id} not found",
                device_id=None,
            )

        token.revoke(reason)
        await self.db.flush()

        audit_logger.log_event(
            event_type="token",
            action="revoke",
            resource_type="device_token",
            resource_id=str(token_id),
            details={
                "device_id": str(token.device_id),
                "reason": reason,
            },
        )

        return token

    async def get_device_counter(self, device_id: UUID) -> int:
        """
        Get the current token counter for a device.

        Returns the highest counter value used, or 0 if no tokens generated.
        """
        result = await self.db.execute(
            select(DeviceToken.counter_value)
            .where(DeviceToken.device_id == device_id)
            .order_by(DeviceToken.counter_value.desc())
            .limit(1)
        )
        counter = result.scalar_one_or_none()
        return counter if counter is not None else 0

    # =========================================================================
    # PRIVATE METHODS
    # =========================================================================

    async def _get_device(self, device_id: UUID) -> Device:
        """Get device by ID, raising error if not found."""
        result = await self.db.execute(
            select(Device).where(Device.id == device_id)
        )
        device = result.scalar_one_or_none()

        if not device:
            logger.error("Device not found", device_id=str(device_id))
            raise DeviceNotFoundError(device_id)

        return device

    async def _get_transaction(self, transaction_id: UUID) -> Transaction:
        """Get transaction by ID, raising error if not found."""
        result = await self.db.execute(
            select(Transaction).where(Transaction.id == transaction_id)
        )
        transaction = result.scalar_one_or_none()

        if not transaction:
            logger.error("Transaction not found", transaction_id=str(transaction_id))
            raise TransactionNotFoundError(transaction_id)

        return transaction

    def _validate_device_status(self, device: Device) -> None:
        """Validate device is in valid state for token generation."""
        if device.status == DeviceStatus.SUSPENDED:
            logger.warning(
                "Token operation attempted for suspended device",
                device_id=str(device.id),
            )
            raise DeviceSuspendedError(device.id, "generate/validate token")

    def _validate_transaction(self, transaction: Transaction, device_id: UUID) -> None:
        """Validate transaction is valid for token generation."""
        if transaction.status != TransactionStatus.COMPLETED:
            logger.error(
                "Transaction not completed",
                transaction_id=str(transaction.id),
                status=transaction.status.value,
            )
            raise TokenGenerationError(
                device_id,
                f"Transaction must be COMPLETED, got {transaction.status.value}",
            )

        if transaction.device_id and transaction.device_id != device_id:
            logger.error(
                "Transaction device mismatch",
                transaction_id=str(transaction.id),
                transaction_device=str(transaction.device_id),
                requested_device=str(device_id),
            )
            raise TokenGenerationError(
                device_id,
                "Transaction belongs to different device",
            )

    def _calculate_days_from_amount(
        self,
        amount: Decimal,
        price_per_day: Decimal,
    ) -> int:
        """Calculate number of days from payment amount."""
        if price_per_day <= 0:
            raise ValueError("price_per_day must be positive")

        days = int(amount / price_per_day)
        return max(1, days)  # Minimum 1 day

    async def _generate_token(
        self,
        device: Device,
        days_valid: int,
        token_type: int,
        transaction_id: Optional[UUID],
    ) -> TokenGenerationResult:
        """
        Internal token generation using OpenPAYGO v2 algorithm.
        """
        try:
            # Get device secret key
            secret_key = self._decrypt_device_secret(device)

            # Get next counter value
            current_counter = await self.get_device_counter(device.id)
            next_counter = (current_counter + 1) % (self.MAX_COUNTER_VALUE + 1)

            # Generate token using OpenPAYGO algorithm
            token_value = self._compute_token_v2(
                secret_key=secret_key,
                counter=next_counter,
                token_type=token_type,
                value=days_valid,
            )

            # Format as XXX-XXX-XXX
            formatted_token = self._format_token(token_value)

            # Calculate expiry
            expires_at = datetime.now(timezone.utc) + timedelta(days=days_valid)

            # Encrypt token for storage
            encrypted_token = encrypt_value(
                formatted_token,
                associated_data=str(device.id),
            )

            # Store token record
            device_token = DeviceToken(
                device_id=device.id,
                transaction_id=transaction_id,
                token_value=encrypted_token,
                token_type=token_type,
                days_added=days_valid,
                counter_value=next_counter,
                expires_at=expires_at,
            )

            self.db.add(device_token)
            await self.db.flush()
            await self.db.refresh(device_token)

            logger.info(
                "Token generated successfully",
                device_id=str(device.id),
                token_id=str(device_token.id),
                token_type=token_type,
                days_valid=days_valid,
                counter=next_counter,
                expires_at=expires_at.isoformat(),
            )

            # Audit log
            audit_logger.log_event(
                event_type="token",
                action="generate",
                resource_type="device_token",
                resource_id=str(device_token.id),
                details={
                    "device_id": str(device.id),
                    "token_type": token_type,
                    "days_valid": days_valid,
                    "counter": next_counter,
                    "transaction_id": str(transaction_id) if transaction_id else None,
                },
            )

            return TokenGenerationResult(
                token=formatted_token,
                token_id=device_token.id,
                device_id=device.id,
                expires_at=expires_at,
                days_added=days_valid,
                counter_value=next_counter,
                token_type=token_type,
                transaction_id=transaction_id,
            )

        except (DeviceNotFoundError, DeviceSuspendedError):
            raise
        except Exception as e:
            logger.error(
                "Token generation failed",
                device_id=str(device.id),
                error=str(e),
                error_type=type(e).__name__,
            )
            raise TokenGenerationError(
                device.id,
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
            return bytes.fromhex(decrypted)
        except Exception as e:
            logger.error(
                "Failed to decrypt device secret",
                device_id=str(device.id),
                error=str(e),
            )
            raise TokenGenerationError(
                device.id,
                "Failed to decrypt device secret key",
                original_error=e,
            )

    def _compute_token_v2(
        self,
        secret_key: bytes,
        counter: int,
        token_type: int,
        value: int,
    ) -> int:
        """
        Compute OpenPAYGO Extended Token v2.

        Algorithm based on:
        https://github.com/EnAccess/OpenPAYGO-Token

        The token encodes:
        - Counter (for replay protection)
        - Token type (ADD_TIME, SET_TIME, etc.)
        - Value (days or other unit)

        Uses HMAC-SHA256 for cryptographic security.
        """
        # Pack token data according to OpenPAYGO v2 spec
        # Format: counter (2 bytes) + type (1 byte) + value (2 bytes)
        token_data = struct.pack(">HBH", counter, token_type, value)

        # Generate HMAC-SHA256
        token_hmac = hmac.new(
            secret_key,
            token_data,
            hashlib.sha256,
        ).digest()

        # Extract 32 bits for token value
        token_int = int.from_bytes(token_hmac[:4], "big")

        # Apply mask and constrain to 9 digits
        token_value = (token_int & self.TOKEN_VALUE_MASK) % self.MAX_TOKEN_VALUE

        # Ensure minimum 9 digits by adding offset if needed
        if token_value < 100000000:
            token_value += 100000000

        return token_value

    def _format_token(self, token_value: int) -> str:
        """Format token as XXX-XXX-XXX."""
        token_str = f"{token_value:09d}"
        return f"{token_str[:3]}-{token_str[3:6]}-{token_str[6:]}"

    def _validate_token_format(self, token: str) -> bool:
        """Validate token format (XXX-XXX-XXX)."""
        if not token:
            return False

        clean_token = token.replace("-", "")

        if len(clean_token) != 9:
            return False

        if not clean_token.isdigit():
            return False

        return True

    async def _validate_token_cryptographically(
        self,
        device: Device,
        token: str,
    ) -> bool:
        """
        Validate token using cryptographic verification.

        This checks if the token could have been generated by this device's
        secret key, even if we don't have the token stored.

        Used for device-side validation requests.
        """
        try:
            secret_key = self._decrypt_device_secret(device)
            clean_token = token.replace("-", "")
            token_int = int(clean_token)

            # Get current counter
            current_counter = await self.get_device_counter(device.id)

            # Check against a window of valid counters (allow some ahead)
            counter_window = 20

            for delta in range(counter_window):
                test_counter = (current_counter + delta + 1) % (self.MAX_COUNTER_VALUE + 1)

                # Try different token types and values
                for token_type in [TokenType.ADD_TIME, TokenType.SET_TIME]:
                    for days in range(1, 366):  # 1-365 days
                        computed = self._compute_token_v2(
                            secret_key=secret_key,
                            counter=test_counter,
                            token_type=token_type,
                            value=days,
                        )

                        if computed == token_int:
                            logger.info(
                                "Token matched cryptographically",
                                device_id=str(device.id),
                                counter=test_counter,
                                token_type=token_type,
                                days=days,
                            )
                            return True

            return False

        except Exception as e:
            logger.error(
                "Cryptographic validation error",
                device_id=str(device.id),
                error=str(e),
            )
            return False
