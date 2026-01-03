# PAYGO Middleware

A production-ready middleware platform connecting payment providers (Wave, QMoney, Apple Pay) to IoT devices (solar systems, e-mobility) using the OpenPAYGO protocol.

## Features

- **Multi-Provider Payment Integration** - Wave, QMoney, Apple Pay with unified API
- **OpenPAYGO Protocol** - Secure token-based device activation
- **Device Metrics** - Real-time telemetry collection and storage
- **Payment Triggers** - Automated billing based on usage, time, or manual triggers
- **LOUD Error Handling** - Every failure is logged with full context
- **Request Tracing** - Unique request IDs through entire flow
- **Webhook Processing** - Idempotent, secure webhook handling

## Quick Start

```bash
# Clone and enter directory
cd backend

# Copy environment file
cp .env.example .env
# Edit .env with your settings

# Start with Docker Compose
docker-compose up -d

# Run migrations
docker-compose exec app alembic upgrade head

# Check health
curl http://localhost:8000/health
```

## API Documentation

Once running, access the interactive API docs:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## Project Structure

```
backend/
├── app/
│   ├── api/v1/              # API endpoints
│   │   ├── openpaygo/       # Device registration, tokens, metrics
│   │   ├── webhooks/        # Payment provider webhooks
│   │   ├── triggers/        # Payment trigger configuration
│   │   └── admin/           # Administrative endpoints
│   ├── core/                # Configuration, security, logging
│   ├── models/              # SQLAlchemy models
│   ├── schemas/             # Pydantic schemas
│   ├── services/            # Business logic
│   │   ├── openpaygo_service.py
│   │   ├── trigger_service.py
│   │   └── payment/         # Payment provider implementations
│   └── utils/               # Utilities (encryption, idempotency)
├── migrations/              # Alembic database migrations
├── tests/                   # Unit and integration tests
├── tools/                   # CLI tools (device simulator)
├── docs/                    # Documentation
└── scripts/                 # Development scripts
```

## Key Endpoints

### Devices

```http
POST /api/v1/openpaygo/device/register    # Register new device
GET  /api/v1/openpaygo/device/{id}        # Get device details
GET  /api/v1/openpaygo/device/{id}/token  # Generate activation token
```

### Metrics

```http
POST /api/v1/openpaygo/metrics            # Submit single metric
POST /api/v1/openpaygo/metrics/batch      # Submit batch metrics
GET  /api/v1/openpaygo/device/{id}/metrics # Get device metrics
```

### Webhooks

```http
POST /api/v1/webhooks/wave      # Wave payment webhook
POST /api/v1/webhooks/qmoney    # QMoney payment webhook
POST /api/v1/webhooks/apple-pay # Apple Pay webhook
```

### Admin

```http
GET  /api/v1/admin/devices        # List devices
GET  /api/v1/admin/transactions   # List transactions
GET  /api/v1/admin/analytics      # Get analytics
```

## Configuration

Key environment variables:

| Variable | Description | Required |
|----------|-------------|----------|
| `DATABASE_URL` | PostgreSQL connection string | Yes |
| `REDIS_URL` | Redis connection string | Yes |
| `SECRET_KEY` | JWT signing key (32+ chars) | Yes |
| `ENCRYPTION_KEY` | AES-256 key (exactly 32 chars) | Yes |
| `WAVE_API_KEY` | Wave API key | For Wave |
| `QMONEY_API_KEY` | QMoney API key | For QMoney |

See `.env.example` for full configuration options.

## Development

### Prerequisites

- Python 3.11+
- PostgreSQL 15+
- Redis 7+
- Docker & Docker Compose (recommended)

### Local Setup

```bash
# Install dependencies
poetry install

# Start services
./scripts/start_dev.sh docker

# Run migrations
alembic upgrade head

# Start server
uvicorn app.main:app --reload
```

### Running Tests

```bash
# All tests
./scripts/run_tests.sh

# Unit tests only
./scripts/run_tests.sh unit

# With coverage
./scripts/run_tests.sh cov
```

### Device Simulator

Test the system with simulated devices:

```bash
# Register a device
python tools/device_simulator.py create --type solar --external-id "SOLAR_001"

# Send metrics
python tools/device_simulator.py send-metrics --device-id <uuid> --energy 50.5

# Stress test
python tools/device_simulator.py stress-test --devices 10 --duration 60
```

## Documentation

- [SETUP.md](docs/SETUP.md) - Installation and configuration
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) - System design and components
- [OPENPAYGO.md](docs/OPENPAYGO.md) - OpenPAYGO protocol integration
- [PAYMENTS.md](docs/PAYMENTS.md) - Payment provider integration
- [TESTING.md](docs/TESTING.md) - Testing guide

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Payment        │     │    PAYGO        │     │    IoT          │
│  Providers      │────▶│   Middleware    │────▶│   Devices       │
│  (Wave/QMoney)  │     │                 │     │  (Solar/eMobility)
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

### Design Principles

1. **LOUD Failures** - All errors logged with context, never silent
2. **Request Tracing** - Unique ID through entire request lifecycle
3. **Idempotency** - Duplicate webhooks handled safely
4. **Provider Abstraction** - Easy to add new payment providers

## Health Checks

```bash
# Basic health
curl http://localhost:8000/health

# Readiness (includes DB/Redis)
curl http://localhost:8000/ready

# Liveness
curl http://localhost:8000/live
```

## Security

- AES-256-GCM encryption for sensitive data at rest
- HMAC-SHA256 webhook signature verification
- JWT tokens for API authentication
- Constant-time comparison for secrets
- Request ID correlation for audit trails

## License

Proprietary - All rights reserved
