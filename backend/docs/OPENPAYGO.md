# OpenPAYGO Protocol Integration

This document describes the OpenPAYGO Token protocol implementation in the PAYGO Middleware.

## Overview

OpenPAYGO is an open-source protocol for Pay-As-You-Go (PAYG) systems. It enables time-limited device access through offline token validation.

## How It Works

### Token Generation Flow

```
1. Customer makes payment → Payment confirmed
2. Middleware generates OpenPAYGO token
3. Customer enters token on device
4. Device validates token locally (no internet needed)
5. Device unlocks for specified duration
```

### Key Components

1. **Secret Key** - 128-bit key shared between middleware and device
2. **Token Counter** - Incrementing counter preventing replay attacks
3. **Token Value** - 9-digit code (XXX-XXX-XXX format)

## API Endpoints

### Register Device

```http
POST /api/v1/openpaygo/device/register
Content-Type: application/json
X-API-Key: your-api-key

{
  "external_id": "SOLAR_001",
  "device_type": "solar",
  "manufacturer": "SunPower",
  "model": "SP-100"
}
```

Response:

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "external_id": "SOLAR_001",
  "device_type": "solar",
  "manufacturer": "SunPower",
  "model": "SP-100",
  "status": "active",
  "secret_key": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
  "created_at": "2024-01-15T10:30:00Z"
}
```

**IMPORTANT:** Store the `secret_key` securely. It must be programmed into the physical device. This is the only time it's returned.

### Generate Token

```http
GET /api/v1/openpaygo/device/{device_id}/token?days_valid=30
X-API-Key: your-api-key
```

Response:

```json
{
  "token": "123-456-789",
  "expires_at": "2024-02-14T10:30:00Z",
  "days_valid": 30,
  "token_count": 5,
  "activation_type": "add_time"
}
```

### Validate Token (Device-Side)

The token validation happens on the device using the shared secret key. The middleware provides a validation endpoint for testing:

```http
POST /api/v1/openpaygo/device/{device_id}/validate
Content-Type: application/json
X-API-Key: your-api-key

{
  "token": "123-456-789"
}
```

Response:

```json
{
  "valid": true,
  "days_added": 30,
  "new_expiry": "2024-02-14T10:30:00Z"
}
```

## Token Types

### ADD_TIME

Adds days to the current expiry (or from now if expired):

```python
# If device expires 2024-01-20 and 30-day token entered on 2024-01-15:
# New expiry = 2024-01-20 + 30 days = 2024-02-19

# If device expired 2024-01-01 and 30-day token entered on 2024-01-15:
# New expiry = 2024-01-15 + 30 days = 2024-02-14
```

### SET_TIME

Sets expiry to exactly N days from token entry:

```python
# 30-day SET_TIME token entered on 2024-01-15:
# New expiry = 2024-01-15 + 30 days = 2024-02-14
# (regardless of previous expiry)
```

### DISABLE_PAYG

Permanently unlocks the device:

```python
# DISABLE_PAYG token entered:
# Device is permanently unlocked
# No further tokens needed
```

### COUNTER_SYNC

Synchronizes token counter without changing time:

```python
# Used when device counter gets out of sync
# Does not affect expiry
```

## Device Metrics

Track device usage for billing:

```http
POST /api/v1/openpaygo/metrics?device_id={device_id}
Content-Type: application/json
X-API-Key: your-api-key

{
  "metric_type": "energy_consumed",
  "value": 50.5,
  "unit": "kWh",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

### Batch Metrics

```http
POST /api/v1/openpaygo/metrics/batch?device_id={device_id}
Content-Type: application/json
X-API-Key: your-api-key

{
  "metrics": [
    {"metric_type": "energy_consumed", "value": 10.5, "unit": "kWh", "timestamp": "..."},
    {"metric_type": "battery_level", "value": 85.0, "unit": "%", "timestamp": "..."},
    {"metric_type": "uptime", "value": 24.0, "unit": "hours", "timestamp": "..."}
  ]
}
```

### Supported Metric Types

| Type | Description | Unit Examples |
|------|-------------|---------------|
| `energy_consumed` | Energy used | kWh |
| `energy_generated` | Energy produced | kWh |
| `battery_level` | Battery charge | % |
| `distance_traveled` | For e-mobility | km |
| `uptime` | Device uptime | hours |
| `temperature` | Device temperature | celsius |
| `voltage` | Power voltage | V |
| `current` | Power current | A |

## Token Security

### Anti-Replay Protection

Each token has an embedded counter that must be greater than the device's stored counter:

```
Device counter: 5
Valid tokens: counter > 5 (6, 7, 8, ...)
Invalid tokens: counter <= 5 (reused tokens)
```

### Secret Key Security

- Generated using cryptographically secure random
- 128-bit (32 hex characters)
- Encrypted at rest using AES-256-GCM
- Never logged or exposed after initial registration

### Token Window

Tokens within a small window of future counters are accepted:

```
Current counter: 10
Accepted range: 11-30 (window of 20)
```

This allows tokens to be pre-generated for offline scenarios.

## Integration Examples

### Python Client

```python
import httpx

class PaygoClient:
    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url
        self.headers = {"X-API-Key": api_key}

    async def register_device(self, external_id: str, device_type: str):
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_url}/api/v1/openpaygo/device/register",
                json={"external_id": external_id, "device_type": device_type},
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()

    async def generate_token(self, device_id: str, days: int = 30):
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.api_url}/api/v1/openpaygo/device/{device_id}/token",
                params={"days_valid": days},
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()

# Usage
client = PaygoClient("http://localhost:8000", "your-api-key")
device = await client.register_device("SOLAR_001", "solar")
token_data = await client.generate_token(device["id"], days=30)
print(f"Token: {token_data['token']}")
```

### Device Firmware (Pseudo-code)

```c
// On device - token validation
#include "openpaygo.h"

// Stored in device
uint8_t secret_key[16] = {...};  // From registration
uint16_t token_counter = 0;
uint32_t expiry_timestamp = 0;

bool validate_token(char* token_str) {
    uint32_t token = parse_token(token_str);  // "123-456-789" → integer

    // Try counters in valid window
    for (int i = 1; i <= TOKEN_WINDOW; i++) {
        uint16_t try_counter = token_counter + i;
        uint32_t expected = generate_token(secret_key, try_counter);

        if (token == expected) {
            token_counter = try_counter;
            uint16_t days = decode_days(token);
            expiry_timestamp = now() + (days * SECONDS_PER_DAY);
            save_to_flash();
            return true;
        }
    }
    return false;
}

bool is_device_active() {
    return now() < expiry_timestamp;
}
```

## Troubleshooting

### Token Not Accepted

1. **Counter out of sync:**
   - Generate a COUNTER_SYNC token
   - Or generate new token with higher counter

2. **Secret key mismatch:**
   - Verify device was programmed with correct key
   - Check for byte order issues

3. **Token expired before entry:**
   - Tokens don't expire, but device time might be wrong
   - Check device RTC

### Device Locked Unexpectedly

1. **Time drift:**
   - Device RTC may have drifted
   - Send time sync command if supported

2. **Token not saved:**
   - Check device flash write succeeded
   - Verify power stability during save

## Best Practices

1. **Pre-generate tokens** for areas with poor connectivity
2. **Store secret keys** securely (HSM or encrypted storage)
3. **Monitor token counter** to detect anomalies
4. **Log all token generations** for audit trails
5. **Implement token delivery** via SMS for offline customers
