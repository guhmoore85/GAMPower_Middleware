# PAYGO Middleware Setup Guide

This guide covers setting up the PAYGO Middleware platform for development and production.

## Prerequisites

- Python 3.11+
- PostgreSQL 15+
- Redis 7+
- Docker & Docker Compose (optional but recommended)

## Quick Start with Docker

The fastest way to get started:

```bash
# Clone and enter directory
cd backend

# Copy environment file
cp .env.example .env

# Start all services
docker-compose up -d

# Run migrations
docker-compose exec app alembic upgrade head

# Verify health
curl http://localhost:8000/health
```

## Manual Setup

### 1. Install Dependencies

Using Poetry (recommended):

```bash
cd backend
poetry install
poetry shell
```

Using pip:

```bash
cd backend
pip install -e ".[dev]"
```

### 2. Configure Environment

Copy and configure environment variables:

```bash
cp .env.example .env
```

**Required Variables:**

| Variable | Description | Example |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql+asyncpg://user:pass@localhost/paygo` |
| `REDIS_URL` | Redis connection string | `redis://localhost:6379/0` |
| `SECRET_KEY` | JWT signing key (32+ chars) | `your-secret-key-here-min-32-chars` |
| `ENCRYPTION_KEY` | AES-256 key (exactly 32 chars) | `32-character-encryption-key-xxx` |

**Payment Provider Variables (Optional for testing):**

```bash
# Wave
WAVE_API_KEY=your_wave_api_key
WAVE_API_URL=https://api.wave.com/v1
WAVE_WEBHOOK_SECRET=your_webhook_secret

# QMoney
QMONEY_API_KEY=your_qmoney_api_key
QMONEY_API_URL=https://api.qmoney.gn/v1
QMONEY_WEBHOOK_SECRET=your_webhook_secret

# Apple Pay
APPLE_PAY_MERCHANT_ID=merchant.yourcompany.com
APPLE_PAY_CERTIFICATE_PATH=/path/to/certificate.pem
APPLE_PAY_PRIVATE_KEY_PATH=/path/to/private_key.pem
```

### 3. Database Setup

Create the database:

```bash
createdb paygo_middleware
```

Run migrations:

```bash
alembic upgrade head
```

Seed initial data (optional):

```bash
python scripts/seed_data.py
```

### 4. Start the Server

Development mode with auto-reload:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Production mode:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

## Verification

### Health Check

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{
  "status": "healthy",
  "version": "1.0.0",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

### API Documentation

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### Database Connection

```bash
curl http://localhost:8000/ready
```

## Production Deployment

### Environment Hardening

1. **Set secure secrets:**
   ```bash
   # Generate secure keys
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. **Configure HTTPS:**
   - Use a reverse proxy (nginx/Caddy)
   - Set `CORS_ORIGINS` to your domains

3. **Database security:**
   - Use SSL connections
   - Create dedicated database user
   - Enable connection pooling

### Docker Production

```bash
# Build production image
docker build -t paygo-middleware:latest --target production .

# Run with external services
docker run -d \
  --name paygo-app \
  -p 8000:8000 \
  -e DATABASE_URL=postgresql+asyncpg://... \
  -e REDIS_URL=redis://... \
  -e SECRET_KEY=... \
  paygo-middleware:latest
```

### Kubernetes

Basic deployment manifest:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: paygo-middleware
spec:
  replicas: 3
  selector:
    matchLabels:
      app: paygo-middleware
  template:
    metadata:
      labels:
        app: paygo-middleware
    spec:
      containers:
      - name: app
        image: paygo-middleware:latest
        ports:
        - containerPort: 8000
        envFrom:
        - secretRef:
            name: paygo-secrets
        livenessProbe:
          httpGet:
            path: /live
            port: 8000
        readinessProbe:
          httpGet:
            path: /ready
            port: 8000
```

## Troubleshooting

### Database Connection Errors

**Error:** `connection refused`

```bash
# Check PostgreSQL is running
pg_isready -h localhost -p 5432

# Check connection string format
# Should be: postgresql+asyncpg://user:password@host:port/database
```

### Redis Connection Errors

**Error:** `Redis connection error`

```bash
# Check Redis is running
redis-cli ping

# Should respond: PONG
```

### Migration Errors

**Error:** `alembic.util.exc.CommandError`

```bash
# Reset migrations (DEVELOPMENT ONLY)
alembic downgrade base
alembic upgrade head
```

### Permission Errors

**Error:** `permission denied for table`

```bash
# Grant permissions to database user
psql -d paygo_middleware -c "GRANT ALL ON ALL TABLES IN SCHEMA public TO paygo_user;"
```

## Next Steps

- Read [ARCHITECTURE.md](ARCHITECTURE.md) for system design
- Read [OPENPAYGO.md](OPENPAYGO.md) for token protocol
- Read [PAYMENTS.md](PAYMENTS.md) for payment integration
- Read [TESTING.md](TESTING.md) for running tests
