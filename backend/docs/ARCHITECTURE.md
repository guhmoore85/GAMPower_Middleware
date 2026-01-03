# PAYGO Middleware Architecture

This document describes the architecture of the PAYGO Middleware platform.

## System Overview

The PAYGO Middleware connects payment providers to IoT devices using the OpenPAYGO protocol. It enables Pay-As-You-Go business models for solar energy systems, e-mobility, and other IoT devices.

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Payment        │     │    PAYGO        │     │    IoT          │
│  Providers      │────▶│   Middleware    │────▶│   Devices       │
│  (Wave/QMoney)  │     │                 │     │  (Solar/eMobility)
└─────────────────┘     └─────────────────┘     └─────────────────┘
        │                       │                       │
        │    Webhooks           │    Metrics            │
        └───────────────────────┴───────────────────────┘
```

## Core Design Principles

### 1. LOUD Failures

Every error is:
- **Logged** with full context (request ID, timestamps, stack traces)
- **Surfaced** with clear, actionable messages
- **Never silent** - no swallowed exceptions

```python
# Example: All errors include context
raise DeviceNotFoundError(
    device_id=device_id,
    context={"external_id": external_id, "search_method": "external_id"}
)
```

### 2. Request Tracing

Every request has a unique ID propagated through all operations:

```python
# Request ID flows through:
# HTTP Request → Service Layer → Database → External APIs → Response
request_id: str = "req_abc123"
```

### 3. Idempotency

All payment operations are idempotent:

```python
# Same webhook processed once
async def process_webhook(payload, idempotency_key):
    if await redis.exists(f"webhook:{idempotency_key}"):
        return {"status": "already_processed"}
    # Process...
```

## Component Architecture

### API Layer (`app/api/`)

FastAPI routers organized by domain:

```
app/api/v1/
├── openpaygo/       # Device registration, tokens, metrics
├── webhooks/        # Payment provider webhooks
├── triggers/        # Payment trigger configuration
└── admin/           # Administrative endpoints
```

**Key Features:**
- Versioned API (v1, v2, etc.)
- Consistent error response format
- Request/response validation via Pydantic
- OpenAPI documentation auto-generated

### Service Layer (`app/services/`)

Business logic implementation:

```
app/services/
├── openpaygo_service.py    # OpenPAYGO protocol implementation
├── trigger_service.py      # Payment trigger evaluation
└── payment/
    ├── base.py             # Abstract payment provider
    ├── factory.py          # Provider factory
    ├── wave_provider.py    # Wave implementation
    ├── qmoney_provider.py  # QMoney implementation
    └── apple_pay_provider.py
```

### Data Layer (`app/models/`, `app/db/`)

SQLAlchemy 2.0 with async support:

```
app/models/
├── device.py           # Device entity
├── customer.py         # Customer entity
├── transaction.py      # Payment transactions
├── device_metric.py    # Device telemetry
├── device_activation.py # Activation records
└── payment_trigger.py  # Trigger configurations
```

### Core Infrastructure (`app/core/`)

Cross-cutting concerns:

```
app/core/
├── config.py       # Environment configuration
├── security.py     # Authentication, encryption
├── exceptions.py   # Custom exception hierarchy
├── logging.py      # Structured logging
└── dependencies.py # FastAPI dependencies
```

## Data Flow

### 1. Device Registration

```
Client                  API                 Service             Database
  │                      │                     │                    │
  │──POST /device/register─▶│                  │                    │
  │                      │──validate──▶        │                    │
  │                      │       │──create device──▶                │
  │                      │       │             │──INSERT device─────▶│
  │                      │       │             │◀───device record────│
  │                      │       │◀─device + secret                 │
  │◀──201 {id, secret}───│                     │                    │
```

### 2. Payment Webhook Processing

```
Provider                API                 Service             Database
  │                      │                     │                    │
  │──POST /webhooks/wave─▶│                    │                    │
  │                      │──verify signature───▶│                   │
  │                      │       │──check idempotency──▶            │
  │                      │       │             │──lookup transaction▶│
  │                      │       │             │◀──transaction───────│
  │                      │       │──update status──▶                │
  │                      │       │             │──UPDATE────────────▶│
  │                      │       │──generate token─▶                │
  │                      │       │◀──token + activation             │
  │◀──200 {received}─────│                     │                    │
```

### 3. Trigger Evaluation

```
Scheduler               Service             Database            Provider
  │                        │                   │                    │
  │──evaluate_triggers────▶│                   │                    │
  │                        │──get active triggers─▶                 │
  │                        │                   │◀──triggers──────────│
  │                        │──for each trigger:                     │
  │                        │  │──get metrics──▶│                    │
  │                        │  │◀──metrics──────│                    │
  │                        │  │──evaluate────▶ │                    │
  │                        │  │ (threshold?)   │                    │
  │                        │  │──if triggered: │                    │
  │                        │  │  │──create transaction──▶           │
  │                        │  │  │──initiate payment────────────────▶│
  │◀──results──────────────│  │                │                    │
```

## Database Schema

### Entity Relationships

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Customer   │◀────│  Transaction │────▶│    Device    │
└──────────────┘     └──────────────┘     └──────────────┘
                                                 │
                     ┌──────────────┐            │
                     │DeviceMetric  │◀───────────┤
                     └──────────────┘            │
                     ┌──────────────┐            │
                     │DeviceActivation│◀─────────┤
                     └──────────────┘            │
                     ┌──────────────┐            │
                     │PaymentTrigger│◀───────────┘
                     └──────────────┘
```

### Key Indexes

```sql
-- Performance-critical indexes
CREATE INDEX idx_device_external_id ON devices(external_id);
CREATE INDEX idx_transaction_status ON transactions(status);
CREATE INDEX idx_metrics_device_time ON device_metrics(device_id, recorded_at);
CREATE INDEX idx_trigger_device_status ON payment_triggers(device_id, status);
```

## Security Architecture

### Authentication Layers

1. **API Key Authentication** - For service-to-service
2. **JWT Bearer Tokens** - For user authentication
3. **Webhook Signatures** - For payment provider webhooks

### Encryption

- **At Rest:** AES-256-GCM for sensitive fields (device secrets)
- **In Transit:** TLS 1.3 required
- **Secrets:** Never logged, always masked

### Request Security

```python
# Every request includes:
- X-Request-ID: Unique request identifier
- X-API-Key: Service authentication
- Authorization: Bearer {JWT} (for user routes)
```

## Error Handling

### Exception Hierarchy

```
BasePaygoError
├── ValidationError (400)
├── AuthenticationError (401)
├── AuthorizationError (403)
├── DeviceNotFoundError (404)
├── DeviceAlreadyExistsError (409)
├── PaymentProviderError (502)
├── DatabaseError (500)
└── EncryptionError (500)
```

### Error Response Format

```json
{
  "error_code": "DEVICE_NOT_FOUND",
  "message": "Device with ID xyz not found",
  "timestamp": "2024-01-15T10:30:00Z",
  "request_id": "req_abc123",
  "details": {
    "device_id": "xyz",
    "search_method": "external_id"
  }
}
```

## Scalability Considerations

### Horizontal Scaling

- Stateless API servers
- Shared Redis for sessions/idempotency
- Connection pooling for PostgreSQL

### Performance Optimizations

- Async database operations
- Cached payment provider instances
- Batch metric submission
- Indexed queries for common operations

### Rate Limiting

```python
# Per-endpoint limits
/api/v1/openpaygo/device/register: 10/minute
/api/v1/openpaygo/metrics: 100/minute
/api/v1/webhooks/*: 1000/minute
```

## Monitoring

### Health Endpoints

- `/health` - Basic health check
- `/ready` - Readiness (DB + Redis connected)
- `/live` - Liveness (app running)

### Metrics (Prometheus)

```
# Key metrics exported
paygo_requests_total{method, endpoint, status}
paygo_request_duration_seconds{method, endpoint}
paygo_payments_total{provider, status}
paygo_tokens_generated_total{device_type}
```

### Logging

```json
{
  "timestamp": "2024-01-15T10:30:00Z",
  "level": "INFO",
  "logger": "app.services.openpaygo_service",
  "message": "Token generated",
  "request_id": "req_abc123",
  "device_id": "device_xyz",
  "days_valid": 30
}
```

## Future Extensibility

### Adding New Payment Providers

1. Create `app/services/payment/new_provider.py`
2. Extend `PaymentProvider` base class
3. Add to `PaymentProviderFactory`
4. Add configuration to `Settings`

### Adding New Device Types

1. Add to `DeviceType` enum
2. Create device-specific metric types
3. Add validation rules if needed

### Adding New Trigger Types

1. Add to `TriggerType` enum
2. Implement evaluation logic in `TriggerService`
3. Add configuration schema validation
