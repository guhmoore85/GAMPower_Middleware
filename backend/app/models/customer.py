"""
Customer Model

Represents customers who own devices and make payments.

CRITICAL: Email and phone validation is enforced.
All validation errors are raised LOUDLY.
"""

import re
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

from sqlalchemy import Index, String, UniqueConstraint, event
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.logging import get_logger
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.transaction import PaymentProvider

if TYPE_CHECKING:
    from app.models.device import Device
    from app.models.transaction import Transaction

logger = get_logger(__name__)

# Email validation regex (simplified but effective)
EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
)

# E.164 phone number regex (+[country code][number], 8-15 digits total)
E164_REGEX = re.compile(r"^\+[1-9]\d{7,14}$")


class Customer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Customer model.

    Attributes:
        id: Unique identifier (UUID)
        external_id: External identifier from partner system
        email: Customer email address (validated)
        phone: Customer phone in E.164 format (validated)
        payment_provider: Preferred payment provider
        payment_provider_id: ID with the payment provider
        metadata: Additional customer data (JSON)
    """

    __tablename__ = "customer"

    # External identifier - unique across all customers
    external_id: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
        nullable=False,
    )

    # Contact info
    email: Mapped[Optional[str]] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=True,
    )

    phone: Mapped[Optional[str]] = mapped_column(
        String(20),
        index=True,
        nullable=True,
    )

    # Payment provider info
    payment_provider: Mapped[PaymentProvider] = mapped_column(
        nullable=False,
    )

    payment_provider_id: Mapped[Optional[str]] = mapped_column(
        String(100),
        index=True,
        nullable=True,
    )

    # Additional metadata
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
    )

    # Relationships
    devices: Mapped[list["Device"]] = relationship(
        "Device",
        back_populates="customer",
        lazy="dynamic",
        foreign_keys="Device.customer_id",
    )

    transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction",
        back_populates="customer",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    # Table-level constraints and indexes
    __table_args__ = (
        # Unique constraint on provider + provider_id combination
        UniqueConstraint(
            "payment_provider",
            "payment_provider_id",
            name="uq_customer_payment_provider_id",
        ),
        # Index for payment provider queries
        Index("ix_customer_payment_provider", "payment_provider"),
    )

    # ==========================================================================
    # Validators
    # ==========================================================================

    @validates("external_id")
    def validate_external_id(self, key: str, value: str) -> str:
        """
        Validate external_id format.

        LOUD: Raises ValueError with details if invalid.
        """
        if not value:
            logger.error("Customer external_id is empty")
            raise ValueError("Customer external_id cannot be empty")

        if len(value) > 100:
            logger.error(
                "Customer external_id too long",
                length=len(value),
                max_length=100,
            )
            raise ValueError(f"Customer external_id exceeds 100 characters: {len(value)}")

        value = value.strip()

        if not value:
            logger.error("Customer external_id is whitespace only")
            raise ValueError("Customer external_id cannot be whitespace only")

        return value

    @validates("email")
    def validate_email(self, key: str, value: Optional[str]) -> Optional[str]:
        """
        Validate email format.

        LOUD: Raises ValueError with details if invalid.
        """
        if value is None:
            return None

        value = value.strip().lower()

        if not value:
            return None

        if len(value) > 255:
            logger.error(
                "Customer email too long",
                length=len(value),
                max_length=255,
            )
            raise ValueError(f"Customer email exceeds 255 characters: {len(value)}")

        if not EMAIL_REGEX.match(value):
            logger.error(
                "Customer email invalid format",
                email=value,
            )
            raise ValueError(
                f"Invalid email format: {value}. "
                "Please provide a valid email address."
            )

        return value

    @validates("phone")
    def validate_phone(self, key: str, value: Optional[str]) -> Optional[str]:
        """
        Validate phone number in E.164 format.

        LOUD: Raises ValueError with details if invalid.
        """
        if value is None:
            return None

        value = value.strip()

        if not value:
            return None

        # Remove any spaces or dashes (common formatting)
        value = re.sub(r"[\s\-\(\)]", "", value)

        # Ensure it starts with +
        if not value.startswith("+"):
            logger.warning(
                "Phone number missing + prefix, adding it",
                original=value,
            )
            value = f"+{value}"

        if len(value) > 20:
            logger.error(
                "Customer phone too long",
                length=len(value),
                max_length=20,
            )
            raise ValueError(f"Customer phone exceeds 20 characters: {len(value)}")

        if not E164_REGEX.match(value):
            logger.error(
                "Customer phone invalid E.164 format",
                phone=value,
            )
            raise ValueError(
                f"Invalid phone format: {value}. "
                "Please use E.164 format (e.g., +12025551234)."
            )

        return value

    @validates("payment_provider")
    def validate_payment_provider(self, key: str, value: Any) -> PaymentProvider:
        """Validate payment_provider is a valid enum value."""
        if isinstance(value, str):
            try:
                return PaymentProvider(value)
            except ValueError:
                logger.error(
                    "Invalid payment_provider",
                    value=value,
                    valid_values=[p.value for p in PaymentProvider],
                )
                raise ValueError(
                    f"Invalid payment_provider: {value}. "
                    f"Valid values: {[p.value for p in PaymentProvider]}"
                )
        return value

    # ==========================================================================
    # Methods
    # ==========================================================================

    def has_payment_method(self) -> bool:
        """Check if customer has a configured payment method."""
        return bool(self.payment_provider_id)

    def get_device_count(self) -> int:
        """Get number of devices for this customer."""
        return self.devices.count() if self.devices else 0


# Event listener for logging customer creation
@event.listens_for(Customer, "after_insert")
def customer_after_insert(mapper, connection, target):
    """Log customer creation."""
    logger.info(
        "Customer created",
        customer_id=str(target.id),
        external_id=target.external_id,
        payment_provider=target.payment_provider.value,
        has_email=bool(target.email),
        has_phone=bool(target.phone),
    )
