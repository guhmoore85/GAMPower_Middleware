# OpenPAYGO Token Testing Guide

This guide covers comprehensive testing of the OpenPAYGO token generation system.

## Overview

The OpenPAYGO Extended Token v2 system generates 9-digit tokens in the format `XXX-XXX-XXX` that can be used to add or set time on PAYGO devices. Each token is:

- **Cryptographically secure**: Uses HMAC-SHA256 for token computation
- **Counter-based**: Each token has a unique counter to prevent replay attacks
- **Device-specific**: Tokens are tied to a specific device's secret key

## Running Tests

### Prerequisites

```bash
cd backend
pip install -r requirements.txt
pip install pytest pytest-asyncio httpx aiosqlite
```

### Unit Tests

Run the comprehensive unit tests:

```bash
# All token-related tests
pytest tests/unit/test_token_service.py -v

# Edge case tests
pytest tests/unit/test_token_edge_cases.py -v

# Run with coverage
pytest tests/unit/test_token_service.py tests/unit/test_token_edge_cases.py --cov=app.services.token_service -v
```

### Integration Tests

Run API endpoint tests:

```bash
# Token API integration tests
pytest tests/integration/test_token_api.py -v

# All integration tests
pytest tests/integration/ -v
```

### All Tests

```bash
# Run everything
pytest tests/ -v

# With coverage report
pytest tests/ --cov=app --cov-report=html
```

## Manual Testing

### Using the Test Script

The `scripts/test_tokens.py` script provides interactive testing capabilities.

#### 1. Run Full Simulation

Creates a test customer, device, and generates multiple tokens:

```bash
python scripts/test_tokens.py simulate
```

Expected output:
- Creates test customer and device
- Generates 4 tokens (7, 30, 90, and 1 day)
- Validates all tokens
- Verifies uniqueness and counter sequence

#### 2. Generate a Token

```bash
# Generate a 30-day ADD_TIME token
python scripts/test_tokens.py generate \
  --device-id <your-device-uuid> \
  --days 30 \
  --token-type ADD_TIME

# Generate a SET_TIME token
python scripts/test_tokens.py generate \
  --device-id <your-device-uuid> \
  --days 90 \
  --token-type SET_TIME
```

#### 3. Validate a Token

```bash
# Validate without consuming
python scripts/test_tokens.py validate \
  --device-id <your-device-uuid> \
  --token "123-456-789"

# Validate and consume
python scripts/test_tokens.py validate \
  --device-id <your-device-uuid> \
  --token "123-456-789" \
  --consume
```

#### 4. List Device Tokens

```bash
python scripts/test_tokens.py list \
  --device-id <your-device-uuid> \
  --limit 20
```

#### 5. Run Performance Benchmark

```bash
# Generate 100 tokens and measure performance
python scripts/test_tokens.py benchmark --count 100

# Use existing device
python scripts/test_tokens.py benchmark \
  --device-id <your-device-uuid> \
  --count 50
```

### Using cURL

#### Get a Simple Token

```bash
curl -X GET "http://localhost:8000/api/v1/openpaygo/device/{device_id}/token?days_valid=30" \
  -H "Authorization: Bearer <jwt_token>"
```

#### Generate Advanced Token

```bash
curl -X POST "http://localhost:8000/api/v1/openpaygo/device/{device_id}/token" \
  -H "Authorization: Bearer <jwt_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "days_valid": 30,
    "token_type": "ADD_TIME",
    "metadata": {"reason": "monthly_payment"}
  }'
```

#### Generate Token from Transaction

```bash
curl -X POST "http://localhost:8000/api/v1/openpaygo/device/{device_id}/token/from-transaction" \
  -H "Authorization: Bearer <jwt_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "transaction_id": "<transaction_uuid>",
    "price_per_day": "1.00"
  }'
```

#### Validate Token

```bash
curl -X POST "http://localhost:8000/api/v1/openpaygo/device/{device_id}/validate" \
  -H "Authorization: Bearer <jwt_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "token": "123-456-789",
    "consume": false
  }'
```

## Test Cases Checklist

### Token Generation

- [ ] Generate token with default days (30)
- [ ] Generate token with custom days (1, 7, 14, 90, 365)
- [ ] Generate ADD_TIME token
- [ ] Generate SET_TIME token
- [ ] Verify token format is XXX-XXX-XXX
- [ ] Verify token is 9 digits
- [ ] Verify counter increments with each generation
- [ ] Verify expiry date is correct
- [ ] Generate token from completed transaction
- [ ] Generate token with custom price_per_day

### Token Validation

- [ ] Validate a freshly generated token
- [ ] Validate token format (XXX-XXX-XXX)
- [ ] Reject invalid format (missing dashes)
- [ ] Reject non-numeric tokens
- [ ] Validate and consume token
- [ ] Reject already-consumed token
- [ ] Reject expired token
- [ ] Reject revoked token

### Error Handling

- [ ] Device not found (404)
- [ ] Suspended device cannot generate tokens
- [ ] Invalid days_valid (0, negative, exceeds max)
- [ ] Transaction not found
- [ ] Pending transaction cannot generate token
- [ ] Missing authentication (401)

### Edge Cases

- [ ] Minimum days (1 day)
- [ ] Maximum days (365 days)
- [ ] First token for device (counter = 1)
- [ ] High counter values (999999+)
- [ ] Concurrent token generation
- [ ] Token uniqueness across multiple generations
- [ ] Different secrets produce different tokens
- [ ] Same inputs produce same token (deterministic)

### Performance

- [ ] Token generation < 100ms
- [ ] Token validation < 50ms
- [ ] Handles 50+ tokens/second

## Expected Response Formats

### Successful Token Generation

```json
{
  "token": "123-456-789",
  "device_id": "uuid",
  "days_valid": 30,
  "token_type": "ADD_TIME",
  "counter": 1,
  "expires_at": "2024-02-15T00:00:00Z",
  "created_at": "2024-01-15T12:00:00Z"
}
```

### Successful Validation

```json
{
  "valid": true,
  "days": 30,
  "token_type": "ADD_TIME",
  "consumed": false
}
```

### Error Response

```json
{
  "error_code": "DEVICE_NOT_FOUND",
  "message": "Device with ID {id} not found",
  "details": {}
}
```

## Common Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `DEVICE_NOT_FOUND` | 404 | Device UUID doesn't exist |
| `DEVICE_SUSPENDED` | 400 | Device is suspended |
| `TOKEN_GENERATION_ERROR` | 400 | Invalid parameters (days, etc.) |
| `INVALID_TOKEN` | 400 | Token format invalid |
| `TOKEN_EXPIRED` | 400 | Token has expired |
| `TOKEN_ALREADY_USED` | 400 | Token was already consumed |
| `TOKEN_REVOKED` | 400 | Token was revoked |
| `TRANSACTION_NOT_FOUND` | 404 | Transaction UUID doesn't exist |

## Troubleshooting

### Token Generation Fails

1. **Check device exists and is active**
   ```sql
   SELECT id, external_id, status FROM device WHERE id = '<uuid>';
   ```

2. **Check device has secret key**
   ```sql
   SELECT id, openpaygo_secret_key IS NOT NULL as has_key FROM device WHERE id = '<uuid>';
   ```

3. **Check days_valid is within range** (1-365 by default)

### Token Validation Fails

1. **Verify token format**: Must be `XXX-XXX-XXX` with digits only
2. **Check token hasn't expired**: Compare `expires_at` with current time
3. **Check token hasn't been used**: `used_at` should be NULL
4. **Check token isn't revoked**: `revoked_at` should be NULL

### Database Queries for Debugging

```sql
-- List recent tokens for a device
SELECT id, counter_value, days_valid, token_type,
       expires_at, used_at, revoked_at, created_at
FROM device_token
WHERE device_id = '<uuid>'
ORDER BY created_at DESC
LIMIT 10;

-- Check current counter for a device
SELECT MAX(counter_value) as current_counter
FROM device_token
WHERE device_id = '<uuid>';

-- Find unused, non-expired tokens
SELECT * FROM device_token
WHERE device_id = '<uuid>'
  AND used_at IS NULL
  AND revoked_at IS NULL
  AND expires_at > NOW();
```

## Security Notes

1. **Secret keys are encrypted at rest** using AES-256
2. **Tokens are stored encrypted** in the database
3. **Counter prevents replay attacks** - each token can only be used once
4. **Token validation is timing-attack resistant** using constant-time comparison
5. **Rate limiting** is applied to all token endpoints

## Performance Benchmarks

Typical performance on standard hardware:

| Operation | Time |
|-----------|------|
| Token Generation | 5-15ms |
| Token Validation | 2-10ms |
| Throughput | 100+ tokens/sec |

Run your own benchmark:
```bash
python scripts/test_tokens.py benchmark --count 100
```
