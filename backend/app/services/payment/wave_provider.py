"""
Wave Payment Provider — Real API Integration

Integrates with the Wave Business APIs:
- Checkout API: Create payment sessions, retrieve status, refund
- Balance API: Check wallet balance, list transactions
- Payout API: Send money to recipients

API Docs: https://docs.wave.com/business
Base URL: https://api.wave.com

CRITICAL: All operations are logged loudly. Secrets are never logged.
"""

import hashlib
import hmac
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

import httpx

from app.core.exceptions import PaymentProviderError
from app.core.logging import get_logger
from app.services.payment.base import (
    PaymentProvider,
    PaymentResult,
    PaymentStatus,
    RefundResult,
)

logger = get_logger(__name__)

# Wave API timeout in seconds
WAVE_API_TIMEOUT = 30.0


@dataclass
class WaveBalance:
    """Result from the Wave Balance API."""

    amount: str
    currency: str


@dataclass
class WavePayoutResult:
    """Result from the Wave Payout API."""

    success: bool
    payout_id: Optional[str] = None
    status: Optional[str] = None
    fee: Optional[str] = None
    timestamp: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    provider_response: Optional[dict] = None


class WavePaymentProvider(PaymentProvider):
    """
    Wave Business payment provider.

    Uses real Wave APIs:
    - POST /v1/checkout/sessions  (create checkout)
    - GET  /v1/checkout/sessions/:id  (retrieve checkout)
    - POST /v1/checkout/sessions/:id/refund  (refund)
    - GET  /v1/balance  (wallet balance)
    - POST /v1/payout  (send payout)
    - GET  /v1/payout/:id  (retrieve payout)
    """

    PROVIDER_NAME = "wave"

    def __init__(
        self,
        api_key: str,
        api_url: str,
        webhook_secret: str,
        *,
        checkout_api_key: str = "",
        balance_api_key: str = "",
        payout_api_key: str = "",
    ):
        super().__init__(api_key=api_key, api_url=api_url, webhook_secret=webhook_secret)
        # Per-API keys (fall back to master key if individual key not set)
        self.checkout_api_key = checkout_api_key or api_key
        self.balance_api_key = balance_api_key or api_key
        self.payout_api_key = payout_api_key or api_key

    def _auth_headers(self, api_key: str) -> dict[str, str]:
        """Build authorization headers for Wave API requests."""
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    # =========================================================================
    # CHECKOUT API — Payment initialization and management
    # =========================================================================

    async def initialize_payment(
        self,
        amount: Decimal,
        currency: str,
        customer_id: UUID,
        *,
        metadata: Optional[dict] = None,
        callback_url: Optional[str] = None,
    ) -> PaymentResult:
        """
        Create a Wave Checkout Session.

        Wave API: POST /v1/checkout/sessions
        Docs: https://docs.wave.com/checkout

        Args:
            amount: Payment amount (string format, no decimals for XOF)
            currency: ISO 4217 currency code (e.g. "XOF", "GMD")
            customer_id: Internal customer UUID (used as client_reference)
            metadata: Additional metadata (client_reference extracted if present)
            callback_url: Success redirect URL

        Returns:
            PaymentResult with wave_launch_url as payment_url
        """
        self._log_request(
            "initialize_payment",
            amount=float(amount),
            currency=currency,
            customer_id=str(customer_id),
            has_metadata=bool(metadata),
        )

        # Build request payload
        error_url = (metadata or {}).get("error_url", callback_url)
        success_url = callback_url or (metadata or {}).get("success_url")
        client_reference = (metadata or {}).get(
            "client_reference", f"gp_{customer_id.hex[:12]}_{uuid4().hex[:8]}"
        )

        payload = {
            "amount": str(int(amount)) if currency == "XOF" else str(amount),
            "currency": currency.upper(),
        }

        if success_url:
            payload["success_url"] = success_url
        if error_url:
            payload["error_url"] = error_url
        if client_reference:
            payload["client_reference"] = client_reference

        # Restrict to specific payer if phone number provided
        restrict_phone = (metadata or {}).get("restrict_payer_mobile")
        if restrict_phone:
            payload["restrict_payer_mobile"] = restrict_phone

        try:
            async with httpx.AsyncClient(timeout=WAVE_API_TIMEOUT) as client:
                response = await client.post(
                    f"{self.api_url}/v1/checkout/sessions",
                    headers=self._auth_headers(self.checkout_api_key),
                    json=payload,
                )

            if response.status_code in (200, 201):
                data = response.json()
                checkout_id = data.get("id", "")
                wave_launch_url = data.get("wave_launch_url", "")

                result = PaymentResult(
                    success=True,
                    transaction_id=checkout_id,
                    status=PaymentStatus.PENDING,
                    payment_url=wave_launch_url,
                    provider_response=data,
                )

                self._log_response(
                    "initialize_payment",
                    success=True,
                    transaction_id=checkout_id,
                    checkout_status=data.get("checkout_status"),
                )

                return result
            else:
                error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                error_code = error_data.get("code", f"http_{response.status_code}")
                error_message = error_data.get("message", response.text[:200])

                logger.error(
                    "Wave checkout creation failed",
                    status_code=response.status_code,
                    error_code=error_code,
                    error_message=error_message,
                )

                return PaymentResult(
                    success=False,
                    status=PaymentStatus.FAILED,
                    error_code=error_code,
                    error_message=error_message,
                    provider_response=error_data,
                )

        except httpx.TimeoutException as e:
            self._log_error("initialize_payment", e)
            raise PaymentProviderError(
                provider=self.PROVIDER_NAME,
                operation="initialize_payment",
                transaction_id=None,
                original_error=e,
            )
        except httpx.HTTPError as e:
            self._log_error("initialize_payment", e)
            raise PaymentProviderError(
                provider=self.PROVIDER_NAME,
                operation="initialize_payment",
                transaction_id=None,
                original_error=e,
            )

    async def get_transaction_status(
        self,
        transaction_id: str,
    ) -> PaymentStatus:
        """
        Get Wave checkout session status.

        Wave API: GET /v1/checkout/sessions/:id

        Args:
            transaction_id: Wave checkout session ID (cos-xxx)

        Returns:
            PaymentStatus mapped from Wave's checkout_status/payment_status
        """
        self._log_request(
            "get_transaction_status",
            transaction_id=transaction_id,
        )

        try:
            async with httpx.AsyncClient(timeout=WAVE_API_TIMEOUT) as client:
                response = await client.get(
                    f"{self.api_url}/v1/checkout/sessions/{transaction_id}",
                    headers=self._auth_headers(self.checkout_api_key),
                )

            if response.status_code == 200:
                data = response.json()
                checkout_status = data.get("checkout_status", "")
                payment_status = data.get("payment_status", "")

                status = self._map_checkout_status(checkout_status, payment_status)

                self._log_response(
                    "get_transaction_status",
                    success=True,
                    checkout_status=checkout_status,
                    payment_status=payment_status,
                    mapped_status=status.value,
                )

                return status
            elif response.status_code == 404:
                logger.warning(
                    "Wave checkout session not found",
                    transaction_id=transaction_id,
                )
                return PaymentStatus.FAILED
            else:
                logger.error(
                    "Wave status check failed",
                    transaction_id=transaction_id,
                    status_code=response.status_code,
                )
                return PaymentStatus.PENDING

        except httpx.HTTPError as e:
            self._log_error("get_transaction_status", e)
            raise PaymentProviderError(
                provider=self.PROVIDER_NAME,
                operation="get_transaction_status",
                transaction_id=transaction_id,
                original_error=e,
            )

    async def refund_transaction(
        self,
        transaction_id: str,
        *,
        amount: Optional[Decimal] = None,
        reason: Optional[str] = None,
    ) -> RefundResult:
        """
        Refund a Wave checkout session (full refund only).

        Wave API: POST /v1/checkout/sessions/:id/refund
        Note: Wave checkout refunds are always full. For partial refunds
        of non-checkout transactions, use the Balance API refund endpoint.

        Args:
            transaction_id: Wave checkout session ID
            amount: Ignored for checkout refunds (always full)
            reason: Reason for refund (logged only, not sent to Wave)

        Returns:
            RefundResult
        """
        self._log_request(
            "refund_transaction",
            transaction_id=transaction_id,
            reason=reason,
        )

        try:
            async with httpx.AsyncClient(timeout=WAVE_API_TIMEOUT) as client:
                response = await client.post(
                    f"{self.api_url}/v1/checkout/sessions/{transaction_id}/refund",
                    headers=self._auth_headers(self.checkout_api_key),
                )

            if response.status_code == 200:
                result = RefundResult(
                    success=True,
                    refund_id=f"refund_{transaction_id}",
                    amount=amount,
                    provider_response={"checkout_id": transaction_id, "refunded": True},
                )

                self._log_response(
                    "refund_transaction",
                    success=True,
                    refund_id=result.refund_id,
                )

                return result
            elif response.status_code == 404:
                return RefundResult(
                    success=False,
                    error_code="checkout-session-not-found",
                    error_message=f"Checkout session {transaction_id} not found",
                )
            else:
                error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                return RefundResult(
                    success=False,
                    error_code=error_data.get("code", f"http_{response.status_code}"),
                    error_message=error_data.get("message", "Refund failed"),
                    provider_response=error_data,
                )

        except httpx.HTTPError as e:
            self._log_error("refund_transaction", e)
            raise PaymentProviderError(
                provider=self.PROVIDER_NAME,
                operation="refund_transaction",
                transaction_id=transaction_id,
                original_error=e,
            )

    # =========================================================================
    # WEBHOOK VERIFICATION
    # =========================================================================

    async def verify_webhook(
        self,
        payload: bytes,
        signature: str,
    ) -> bool:
        """
        Verify Wave webhook signature (Signing Secret strategy).

        Wave sends the signature in the Wave-Signature header:
            Wave-Signature: t=<timestamp>,v1=<signature>[,v1=<signature>]

        Verification:
        1. Extract timestamp and v1 signatures from header
        2. Concatenate: timestamp + raw body
        3. HMAC-SHA256 with webhook_secret
        4. Compare against provided signature(s)
        5. Reject if timestamp is older than 5 minutes

        Args:
            payload: Raw request body bytes
            signature: Wave-Signature header value

        Returns:
            True if signature is valid and timestamp is fresh
        """
        self._log_request(
            "verify_webhook",
            payload_size=len(payload),
            signature_length=len(signature) if signature else 0,
        )

        if not signature or not self.webhook_secret:
            logger.warning(
                "Webhook verification skipped - no signature or secret configured",
                signature_provided=bool(signature),
                secret_configured=bool(self.webhook_secret),
            )
            # If no webhook secret is configured, skip verification
            # This allows the system to work before webhook secret is set up
            return not self.webhook_secret

        try:
            # Parse Wave-Signature header: t=<timestamp>,v1=<sig1>[,v1=<sig2>]
            parts = signature.split(",")
            timestamp = None
            signatures = []

            for part in parts:
                part = part.strip()
                if part.startswith("t="):
                    timestamp = part[2:]
                elif part.startswith("v1="):
                    signatures.append(part[3:])

            if not timestamp or not signatures:
                logger.warning(
                    "Invalid Wave-Signature format",
                    signature=signature[:50],
                )
                return False

            # Check timestamp freshness (5 minute window)
            try:
                webhook_time = int(timestamp)
                current_time = int(time.time())
                if abs(current_time - webhook_time) > 300:
                    logger.warning(
                        "Wave webhook timestamp too old",
                        webhook_time=webhook_time,
                        current_time=current_time,
                        delta_seconds=abs(current_time - webhook_time),
                    )
                    return False
            except ValueError:
                logger.warning("Invalid timestamp in Wave-Signature")
                return False

            # Compute expected signature: HMAC-SHA256(secret, timestamp + body)
            signed_payload = f"{timestamp}".encode() + payload
            expected_signature = hmac.new(
                self.webhook_secret.encode("utf-8"),
                signed_payload,
                hashlib.sha256,
            ).hexdigest()

            # Compare against all provided v1 signatures
            is_valid = any(
                hmac.compare_digest(expected_signature, sig)
                for sig in signatures
            )

            self._log_response(
                "verify_webhook",
                success=is_valid,
            )

            return is_valid

        except Exception as e:
            logger.error(
                "Wave webhook verification error",
                error=str(e),
            )
            return False

    # =========================================================================
    # WEBHOOK PAYLOAD PARSING
    # =========================================================================

    def parse_webhook_payload(
        self,
        payload: dict,
    ) -> dict:
        """
        Parse Wave webhook event payload.

        Wave webhook event format:
        {
            "id": "evt-xxx",
            "type": "checkout.session.completed",
            "data": {
                "id": "cos-xxx",
                "amount": "1000",
                "currency": "XOF",
                "checkout_status": "complete",
                "payment_status": "succeeded",
                "transaction_id": "FAH.1234.5678",
                "client_reference": "...",
                ...
            }
        }
        """
        self._log_request(
            "parse_webhook_payload",
            event_type=payload.get("type"),
        )

        try:
            event_type = payload.get("type", "")
            data = payload.get("data", {})

            # Map Wave event types to our status
            status = self._map_event_to_status(event_type, data)

            parsed = {
                "event_type": event_type,
                "transaction_id": data.get("id"),
                "wave_transaction_id": data.get("transaction_id"),
                "status": status,
                "amount": Decimal(data.get("amount", "0")),
                "currency": data.get("currency"),
                "client_reference": data.get("client_reference"),
                "metadata": {
                    "checkout_status": data.get("checkout_status"),
                    "payment_status": data.get("payment_status"),
                    "business_name": data.get("business_name"),
                    "last_payment_error": data.get("last_payment_error"),
                },
            }

            logger.debug(
                "Wave webhook parsed",
                event_type=event_type,
                transaction_id=parsed["transaction_id"],
                status=parsed["status"],
            )

            return parsed

        except Exception as e:
            logger.error(
                "Failed to parse Wave webhook",
                error=str(e),
                payload_keys=list(payload.keys()),
            )
            raise PaymentProviderError(
                provider=self.PROVIDER_NAME,
                operation="parse_webhook",
                original_error=e,
            )

    # =========================================================================
    # BALANCE API
    # =========================================================================

    async def get_balance(self) -> WaveBalance:
        """
        Get Wave wallet balance.

        Wave API: GET /v1/balance

        Returns:
            WaveBalance with amount and currency
        """
        self._log_request("get_balance")

        try:
            async with httpx.AsyncClient(timeout=WAVE_API_TIMEOUT) as client:
                response = await client.get(
                    f"{self.api_url}/v1/balance",
                    headers=self._auth_headers(self.balance_api_key),
                )

            if response.status_code == 200:
                data = response.json()
                balance = WaveBalance(
                    amount=data.get("amount", "0"),
                    currency=data.get("currency", ""),
                )

                self._log_response(
                    "get_balance",
                    success=True,
                    currency=balance.currency,
                )

                return balance
            else:
                error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                raise PaymentProviderError(
                    provider=self.PROVIDER_NAME,
                    operation="get_balance",
                    provider_message=error_data.get("message", f"HTTP {response.status_code}"),
                )

        except httpx.HTTPError as e:
            self._log_error("get_balance", e)
            raise PaymentProviderError(
                provider=self.PROVIDER_NAME,
                operation="get_balance",
                original_error=e,
            )

    async def get_transactions(
        self,
        date: Optional[str] = None,
        after: Optional[str] = None,
    ) -> dict:
        """
        List Wave wallet transactions for a specific day.

        Wave API: GET /v1/transactions

        Args:
            date: Date string (YYYY-MM-DD), defaults to today
            after: Pagination cursor from previous response

        Returns:
            Dict with items list and page_info for pagination
        """
        self._log_request("get_transactions", date=date)

        params = {}
        if date:
            params["date"] = date
        if after:
            params["after"] = after

        try:
            async with httpx.AsyncClient(timeout=WAVE_API_TIMEOUT) as client:
                response = await client.get(
                    f"{self.api_url}/v1/transactions",
                    headers=self._auth_headers(self.balance_api_key),
                    params=params,
                )

            if response.status_code == 200:
                data = response.json()

                self._log_response(
                    "get_transactions",
                    success=True,
                    count=len(data.get("items", [])),
                )

                return data
            else:
                error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                raise PaymentProviderError(
                    provider=self.PROVIDER_NAME,
                    operation="get_transactions",
                    provider_message=error_data.get("message", f"HTTP {response.status_code}"),
                )

        except httpx.HTTPError as e:
            self._log_error("get_transactions", e)
            raise PaymentProviderError(
                provider=self.PROVIDER_NAME,
                operation="get_transactions",
                original_error=e,
            )

    # =========================================================================
    # PAYOUT API
    # =========================================================================

    async def create_payout(
        self,
        mobile: str,
        amount: Decimal,
        currency: str,
        *,
        name: Optional[str] = None,
        client_reference: Optional[str] = None,
        payment_reason: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> WavePayoutResult:
        """
        Send a payout to a recipient.

        Wave API: POST /v1/payout

        Args:
            mobile: Recipient phone number (E.164 format, e.g. +2201234567)
            amount: Amount to send (no decimals for XOF)
            currency: ISO 4217 currency code
            name: Recipient name (optional)
            client_reference: Your reference string (optional, max 255 chars)
            payment_reason: Reason shown to recipient (optional, max 40 chars)
            idempotency_key: Idempotency key (UUID recommended)

        Returns:
            WavePayoutResult
        """
        self._log_request(
            "create_payout",
            amount=float(amount),
            currency=currency,
            has_name=bool(name),
        )

        payload = {
            "receive_amount": str(int(amount)) if currency == "XOF" else str(amount),
            "currency": currency.upper(),
            "mobile": mobile,
        }

        if name:
            payload["name"] = name
        if client_reference:
            payload["client_reference"] = client_reference
        if payment_reason:
            payload["payment_reason"] = payment_reason[:40]

        headers = self._auth_headers(self.payout_api_key)
        headers["Idempotency-Key"] = idempotency_key or uuid4().hex

        try:
            async with httpx.AsyncClient(timeout=WAVE_API_TIMEOUT) as client:
                response = await client.post(
                    f"{self.api_url}/v1/payout",
                    headers=headers,
                    json=payload,
                )

            if response.status_code in (200, 201):
                data = response.json()

                result = WavePayoutResult(
                    success=data.get("status") != "failed",
                    payout_id=data.get("id"),
                    status=data.get("status"),
                    fee=data.get("fee"),
                    timestamp=data.get("timestamp"),
                    provider_response=data,
                )

                if data.get("payout_error"):
                    result.success = False
                    result.error_code = data["payout_error"].get("code")
                    result.error_message = data["payout_error"].get("message")

                self._log_response(
                    "create_payout",
                    success=result.success,
                    payout_id=result.payout_id,
                    status=result.status,
                )

                return result
            else:
                error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}

                return WavePayoutResult(
                    success=False,
                    error_code=error_data.get("code", f"http_{response.status_code}"),
                    error_message=error_data.get("message", response.text[:200]),
                    provider_response=error_data,
                )

        except httpx.HTTPError as e:
            self._log_error("create_payout", e)
            raise PaymentProviderError(
                provider=self.PROVIDER_NAME,
                operation="create_payout",
                original_error=e,
            )

    async def get_payout_status(self, payout_id: str) -> WavePayoutResult:
        """
        Retrieve payout status.

        Wave API: GET /v1/payout/:id

        Args:
            payout_id: Wave payout ID (pt-xxx)

        Returns:
            WavePayoutResult with current status
        """
        self._log_request("get_payout_status", payout_id=payout_id)

        try:
            async with httpx.AsyncClient(timeout=WAVE_API_TIMEOUT) as client:
                response = await client.get(
                    f"{self.api_url}/v1/payout/{payout_id}",
                    headers=self._auth_headers(self.payout_api_key),
                )

            if response.status_code == 200:
                data = response.json()
                result = WavePayoutResult(
                    success=data.get("status") == "succeeded",
                    payout_id=data.get("id"),
                    status=data.get("status"),
                    fee=data.get("fee"),
                    timestamp=data.get("timestamp"),
                    provider_response=data,
                )

                self._log_response(
                    "get_payout_status",
                    success=True,
                    payout_id=payout_id,
                    status=result.status,
                )

                return result
            else:
                raise PaymentProviderError(
                    provider=self.PROVIDER_NAME,
                    operation="get_payout_status",
                    transaction_id=payout_id,
                    provider_message=f"HTTP {response.status_code}",
                )

        except httpx.HTTPError as e:
            self._log_error("get_payout_status", e)
            raise PaymentProviderError(
                provider=self.PROVIDER_NAME,
                operation="get_payout_status",
                transaction_id=payout_id,
                original_error=e,
            )

    # =========================================================================
    # STATUS MAPPING HELPERS
    # =========================================================================

    def _map_checkout_status(
        self,
        checkout_status: str,
        payment_status: str,
    ) -> PaymentStatus:
        """Map Wave checkout_status + payment_status to our PaymentStatus."""
        if checkout_status == "complete" and payment_status == "succeeded":
            return PaymentStatus.COMPLETED
        elif checkout_status == "expired":
            return PaymentStatus.FAILED
        elif payment_status == "cancelled":
            return PaymentStatus.FAILED
        else:
            return PaymentStatus.PENDING

    def _map_event_to_status(self, event_type: str, data: dict) -> str:
        """Map Wave webhook event type to our status string."""
        event_map = {
            "checkout.session.completed": PaymentStatus.COMPLETED.value,
            "checkout.session.expired": PaymentStatus.FAILED.value,
            "merchant.payment_received": PaymentStatus.COMPLETED.value,
        }

        if event_type in event_map:
            return event_map[event_type]

        # Fall back to checking payment_status in data
        payment_status = data.get("payment_status", "")
        if payment_status == "succeeded":
            return PaymentStatus.COMPLETED.value
        elif payment_status == "cancelled":
            return PaymentStatus.FAILED.value

        return PaymentStatus.PENDING.value

    def _map_status(self, provider_status: Optional[str]) -> str:
        """Map Wave status string to our PaymentStatus (legacy compat)."""
        status_map = {
            "pending": PaymentStatus.PENDING.value,
            "processing": PaymentStatus.PENDING.value,
            "open": PaymentStatus.PENDING.value,
            "complete": PaymentStatus.COMPLETED.value,
            "completed": PaymentStatus.COMPLETED.value,
            "succeeded": PaymentStatus.COMPLETED.value,
            "successful": PaymentStatus.COMPLETED.value,
            "failed": PaymentStatus.FAILED.value,
            "cancelled": PaymentStatus.FAILED.value,
            "expired": PaymentStatus.FAILED.value,
            "refunded": PaymentStatus.REFUNDED.value,
            "reversed": PaymentStatus.REFUNDED.value,
        }
        return status_map.get(
            (provider_status or "").lower(),
            PaymentStatus.PENDING.value,
        )
