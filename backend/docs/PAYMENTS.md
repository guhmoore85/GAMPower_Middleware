# Payment Provider Integration

This document covers payment provider integration in the PAYGO Middleware.

## Supported Providers

| Provider | Region | Currencies | Status |
|----------|--------|------------|--------|
| Wave | West Africa | XOF, GNF, USD | Production Ready |
| QMoney | Guinea | GNF | Production Ready |
| Apple Pay | Global | USD, EUR, etc. | Production Ready |

## Architecture

### Provider Abstraction

All payment providers implement a common interface:

```python
class PaymentProvider(ABC):
    @abstractmethod
    async def initiate_payment(
        self,
        amount: Decimal,
        currency: str,
        customer_phone: str,
        reference: str,
        **kwargs,
    ) -> PaymentResult:
        """Initiate a payment request."""
        pass

    @abstractmethod
    async def check_status(
        self, transaction_id: str
    ) -> PaymentResult:
        """Check payment status."""
        pass

    @abstractmethod
    async def verify_webhook(
        self, payload: bytes, signature: str
    ) -> bool:
        """Verify webhook signature."""
        pass

    @abstractmethod
    async def refund(
        self,
        transaction_id: str,
        amount: Decimal,
        reason: str,
    ) -> PaymentResult:
        """Process refund."""
        pass
```

### Factory Pattern

Get provider instances via factory:

```python
from app.services.payment.factory import get_payment_provider

# By string name
provider = get_payment_provider("wave")

# By enum
from app.models.transaction import PaymentProvider as ProviderEnum
provider = get_payment_provider(ProviderEnum.QMONEY)
```

## Wave Integration

### Configuration

```bash
WAVE_API_KEY=wave_live_xxx
WAVE_API_URL=https://api.wave.com/v1
WAVE_WEBHOOK_SECRET=whsec_xxx
```

### Payment Flow

```
1. Customer initiates payment on your app
2. Middleware calls Wave API to create payment request
3. Customer receives push notification on Wave app
4. Customer confirms payment in Wave app
5. Wave sends webhook to middleware
6. Middleware generates token and activates device
```

### API Usage

```python
from app.services.payment.factory import get_payment_provider

wave = get_payment_provider("wave")

# Initiate payment
result = await wave.initiate_payment(
    amount=Decimal("5000"),
    currency="XOF",
    customer_phone="+221771234567",
    reference=f"device_{device_id}_payment_{uuid4().hex[:8]}",
)

if result.success:
    print(f"Payment initiated: {result.provider_transaction_id}")
else:
    print(f"Payment failed: {result.error_message}")
```

### Webhook Handling

```python
@router.post("/webhooks/wave")
async def wave_webhook(
    request: Request,
    x_wave_signature: str = Header(...),
):
    payload = await request.body()

    # Verify signature
    provider = get_payment_provider("wave")
    if not await provider.verify_webhook(payload, x_wave_signature):
        logger.warning("Invalid Wave webhook signature")
        return {"status": "invalid_signature"}

    # Process webhook
    data = await request.json()
    if data["event"] == "payment.completed":
        await process_payment_completion(data)

    return {"status": "received"}
```

## QMoney Integration

### Configuration

```bash
QMONEY_API_KEY=qm_live_xxx
QMONEY_API_URL=https://api.qmoney.gn/v1
QMONEY_WEBHOOK_SECRET=qm_whsec_xxx
```

### Payment Flow

```
1. Customer provides phone number
2. Middleware creates payment request
3. Customer receives USSD prompt or SMS
4. Customer enters PIN to confirm
5. QMoney sends webhook
6. Middleware activates device
```

### API Usage

```python
qmoney = get_payment_provider("qmoney")

result = await qmoney.initiate_payment(
    amount=Decimal("50000"),
    currency="GNF",
    customer_phone="+224621234567",
    reference=f"paygo_{device_id}",
)
```

## Apple Pay Integration

### Configuration

```bash
APPLE_PAY_MERCHANT_ID=merchant.yourcompany.paygo
APPLE_PAY_CERTIFICATE_PATH=/secrets/apple-pay-cert.pem
APPLE_PAY_PRIVATE_KEY_PATH=/secrets/apple-pay-key.pem
```

### Payment Flow

```
1. User clicks "Pay with Apple Pay" button
2. Frontend requests merchant validation
3. Middleware validates with Apple
4. User authenticates with Face ID/Touch ID
5. Frontend sends payment token
6. Middleware processes token
7. Device activated
```

### Merchant Validation

```python
apple_pay = get_payment_provider("apple_pay")

# Called from frontend during Apple Pay session
validation_data = await apple_pay.validate_merchant(
    validation_url="https://apple-pay-gateway.apple.com/...",
    domain_name="yourcompany.com",
)
```

### Payment Processing

```python
result = await apple_pay.initiate_payment(
    amount=Decimal("49.99"),
    currency="USD",
    customer_phone="+1234567890",
    reference=f"order_{order_id}",
    payment_token="eyJwYXltZW50RGF0YS...",  # From Apple Pay JS
)
```

## Payment Triggers

Automate payments based on device usage or time:

### Usage-Based Trigger

Charge when energy consumption exceeds threshold:

```python
from app.models.payment_trigger import TriggerType

trigger_config = {
    "trigger_type": "usage_based",
    "configuration": {
        "metric_type": "energy_consumed",
        "threshold": 100.0,  # kWh
        "amount_per_unit": "0.15",  # $0.15 per kWh over threshold
        "currency": "USD"
    }
}
```

### Time-Based Trigger

Charge on recurring schedule:

```python
trigger_config = {
    "trigger_type": "time_based",
    "configuration": {
        "interval_days": 30,
        "amount": "25.00",
        "currency": "USD"
    }
}
```

### Manual Trigger

On-demand payment request:

```python
trigger_config = {
    "trigger_type": "manual",
    "configuration": {
        "amount": "10.00",
        "currency": "USD",
        "description": "Top-up payment"
    }
}
```

## Transaction Management

### Transaction States

```
PENDING → PROCESSING → COMPLETED
                    ↘ FAILED
                    ↘ EXPIRED

COMPLETED → REFUND_PENDING → REFUNDED
```

### Query Transactions

```http
GET /api/v1/admin/transactions?status=completed&provider=wave&limit=50
X-API-Key: admin-api-key
```

### Transaction Details

```http
GET /api/v1/admin/transactions/{transaction_id}
X-API-Key: admin-api-key
```

Response:

```json
{
  "id": "txn_xxx",
  "amount": "50.00",
  "currency": "USD",
  "status": "completed",
  "provider": "wave",
  "provider_transaction_id": "wave_xxx",
  "customer_id": "cust_xxx",
  "device_id": "dev_xxx",
  "created_at": "2024-01-15T10:30:00Z",
  "completed_at": "2024-01-15T10:31:00Z",
  "activation": {
    "id": "act_xxx",
    "token": "123-456-789",
    "days_added": 30,
    "expires_at": "2024-02-14T10:31:00Z"
  }
}
```

## Error Handling

### Provider Errors

All provider errors are wrapped in `PaymentProviderError`:

```python
try:
    result = await provider.initiate_payment(...)
except PaymentProviderError as e:
    logger.error(
        "Payment initiation failed",
        provider=e.provider,
        operation=e.operation,
        error=e.provider_message,
    )
```

### Common Error Codes

| Code | Description | Action |
|------|-------------|--------|
| `INSUFFICIENT_FUNDS` | Customer has insufficient balance | Notify customer |
| `INVALID_PHONE` | Phone number format invalid | Validate input |
| `PROVIDER_UNAVAILABLE` | Provider API down | Retry with backoff |
| `DUPLICATE_TRANSACTION` | Idempotency key exists | Return existing result |
| `RATE_LIMITED` | Too many requests | Implement backoff |

### Retry Logic

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
)
async def initiate_with_retry(provider, **kwargs):
    return await provider.initiate_payment(**kwargs)
```

## Webhooks

### Security

All webhooks must be verified:

```python
# Signature verification happens automatically
# Invalid signatures are logged and rejected

# Webhook endpoints always return 200 to prevent retries
# Actual processing is async
```

### Idempotency

Webhooks are processed exactly once:

```python
# Redis stores processed webhook IDs
# Duplicate webhooks return early with "already_processed"
```

### Webhook Payload Examples

**Wave:**
```json
{
  "event": "payment.completed",
  "data": {
    "id": "wave_txn_xxx",
    "amount": "5000",
    "currency": "XOF",
    "status": "completed",
    "metadata": {
      "reference": "device_xxx_payment_yyy"
    }
  }
}
```

**QMoney:**
```json
{
  "type": "transaction.success",
  "transaction": {
    "id": "qm_xxx",
    "amount": 50000,
    "currency": "GNF",
    "phone": "+224621234567",
    "reference": "paygo_xxx"
  }
}
```

## Testing

### Mock Providers

In development/test, providers return mock responses:

```python
# Set environment
ENVIRONMENT=development

# Mock payments always succeed after 2 seconds
result = await provider.initiate_payment(...)
assert result.success == True
```

### Test Webhooks

```bash
# Generate test webhook signature
python -c "
from app.core.security import generate_webhook_test_signature
import json

payload = json.dumps({'event': 'payment.completed', 'data': {...}}).encode()
secret = 'your_webhook_secret'
sig = generate_webhook_test_signature(payload, secret)
print(sig)
"

# Send test webhook
curl -X POST http://localhost:8000/api/v1/webhooks/wave \
  -H "Content-Type: application/json" \
  -H "X-Wave-Signature: $SIGNATURE" \
  -d '{"event": "payment.completed", ...}'
```

## Best Practices

1. **Always verify webhooks** - Never trust unsigned requests
2. **Implement idempotency** - Handle duplicate webhooks gracefully
3. **Log everything** - Payment operations need full audit trails
4. **Handle timeouts** - Set reasonable timeouts for provider calls
5. **Monitor provider health** - Track success rates and latencies
6. **Secure credentials** - Use secrets management (Vault, AWS Secrets)
7. **Test failure scenarios** - Ensure graceful degradation
