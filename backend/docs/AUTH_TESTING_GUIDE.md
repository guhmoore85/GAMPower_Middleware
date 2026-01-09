# JWT Authentication Testing Guide

This guide covers comprehensive testing of the JWT authentication system.

## Overview

The authentication system provides:
- JWT access tokens (30 minute expiry)
- Refresh tokens (7 day expiry with rotation)
- Role-based access control (admin, technician, support, readonly)
- Rate limiting for failed login attempts
- Password complexity requirements

## Running Tests

### Prerequisites

```bash
cd backend
pip install -r requirements.txt
pip install pytest pytest-asyncio httpx aiosqlite
```

### Unit Tests

```bash
# Auth service tests
pytest tests/unit/test_auth_service.py -v

# Security tests
pytest tests/unit/test_auth_security.py -v

# All auth-related unit tests
pytest tests/unit/test_auth*.py -v
```

### Integration Tests

```bash
# Auth API endpoint tests
pytest tests/integration/test_auth_api.py -v

# Role-based access control tests
pytest tests/integration/test_rbac.py -v

# All auth integration tests
pytest tests/integration/test_auth*.py tests/integration/test_rbac.py -v
```

### Full Test Suite with Coverage

```bash
pytest tests/ --cov=app.services.auth_service --cov=app.core.security -v
```

## Manual Testing with cURL

### Setup

First, ensure you have an admin user. Run the seed script:

```bash
cd backend
python scripts/create_admin.py
```

Set your base URL:
```bash
export BASE_URL="http://localhost:8000"
```

### 1. Login

#### Successful Login
```bash
curl -X POST "${BASE_URL}/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin",
    "password": "YourAdminPassword123"
  }'
```

Expected response:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "dG9rZW5fc2VjcmV0...",
  "token_type": "bearer",
  "expires_in": 1800,
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
    "is_active": true
  }
}
```

Save the tokens:
```bash
export ACCESS_TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
export REFRESH_TOKEN="dG9rZW5fc2VjcmV0..."
```

#### Login with Email
```bash
curl -X POST "${BASE_URL}/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin@example.com",
    "password": "YourAdminPassword123"
  }'
```

#### Invalid Credentials (401)
```bash
curl -X POST "${BASE_URL}/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin",
    "password": "WrongPassword"
  }'
```

Expected: 401 Unauthorized

### 2. Get Current User (/me)

```bash
curl -X GET "${BASE_URL}/api/v1/auth/me" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Expected response:
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "username": "admin",
  "email": "admin@example.com",
  "full_name": "Admin User",
  "role": "admin",
  "is_active": true,
  "last_login_at": "2024-01-15T12:00:00Z",
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-15T12:00:00Z"
}
```

### 3. Refresh Token

```bash
curl -X POST "${BASE_URL}/api/v1/auth/refresh" \
  -H "Content-Type: application/json" \
  -d "{
    \"refresh_token\": \"${REFRESH_TOKEN}\"
  }"
```

Expected response (with rotated tokens):
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "bmV3X3JlZnJlc2hfdG9rZW4...",
  "token_type": "bearer",
  "expires_in": 1800
}
```

**Important:** Save the new refresh token! The old one is now revoked.

### 4. List Active Sessions

```bash
curl -X GET "${BASE_URL}/api/v1/auth/sessions" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Expected response:
```json
[
  {
    "id": "session-uuid",
    "device_info": "curl/7.68.0",
    "ip_address": "127.0.0.1",
    "created_at": "2024-01-15T12:00:00Z",
    "expires_at": "2024-01-22T12:00:00Z"
  }
]
```

### 5. Logout

#### Logout Current Session
```bash
curl -X POST "${BASE_URL}/api/v1/auth/logout" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"refresh_token\": \"${REFRESH_TOKEN}\"
  }"
```

#### Logout All Sessions
```bash
curl -X POST "${BASE_URL}/api/v1/auth/logout-all" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

### 6. Change Password

```bash
curl -X POST "${BASE_URL}/api/v1/auth/change-password" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "current_password": "OldPassword123",
    "new_password": "NewSecurePassword456"
  }'
```

### 7. User Management (Admin Only)

#### List Users
```bash
curl -X GET "${BASE_URL}/api/v1/users" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

#### List Users with Filters
```bash
# Filter by role
curl -X GET "${BASE_URL}/api/v1/users?role=technician" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"

# Filter by status
curl -X GET "${BASE_URL}/api/v1/users?is_active=true" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"

# Search by username/email
curl -X GET "${BASE_URL}/api/v1/users?search=john" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"

# Pagination
curl -X GET "${BASE_URL}/api/v1/users?page=1&page_size=10" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

#### Create User
```bash
curl -X POST "${BASE_URL}/api/v1/users" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "newtechnician",
    "email": "tech@example.com",
    "password": "TechPassword123",
    "full_name": "New Technician",
    "role": "technician"
  }'
```

#### Get User Details
```bash
curl -X GET "${BASE_URL}/api/v1/users/{user_id}" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

#### Update User
```bash
curl -X PATCH "${BASE_URL}/api/v1/users/{user_id}" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "full_name": "Updated Name",
    "role": "support"
  }'
```

#### Deactivate User
```bash
curl -X DELETE "${BASE_URL}/api/v1/users/{user_id}" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

#### Activate User
```bash
curl -X POST "${BASE_URL}/api/v1/users/{user_id}/activate" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

#### Admin Password Reset
```bash
curl -X POST "${BASE_URL}/api/v1/users/{user_id}/reset-password" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "new_password": "ResetPassword123"
  }'
```

## Security Test Cases

### Rate Limiting Test

Run this script to test rate limiting:

```bash
#!/bin/bash
# Test rate limiting (should get 429 after 5 failures)

for i in {1..7}; do
  echo "Attempt $i:"
  curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST "${BASE_URL}/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -d '{
      "username": "testuser",
      "password": "wrongpassword"
    }'
  sleep 0.5
done
```

Expected: First 5 return 401, 6th and 7th return 429.

### Invalid Token Tests

```bash
# Expired/Invalid Token
curl -X GET "${BASE_URL}/api/v1/auth/me" \
  -H "Authorization: Bearer invalid_token_here"
# Expected: 401

# Missing Token
curl -X GET "${BASE_URL}/api/v1/auth/me"
# Expected: 401

# Malformed Authorization Header
curl -X GET "${BASE_URL}/api/v1/auth/me" \
  -H "Authorization: NotBearer some_token"
# Expected: 401
```

### Role-Based Access Control Tests

```bash
# Login as technician
TECH_TOKEN=$(curl -s -X POST "${BASE_URL}/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "technician", "password": "TechPassword123"}' \
  | jq -r '.access_token')

# Technician tries to access user management (should fail)
curl -X GET "${BASE_URL}/api/v1/users" \
  -H "Authorization: Bearer ${TECH_TOKEN}"
# Expected: 403 Forbidden
```

## Test Cases Checklist

### Authentication

- [ ] Login with valid username and password
- [ ] Login with email instead of username
- [ ] Login fails with wrong password (401)
- [ ] Login fails with non-existent user (401)
- [ ] Login fails for inactive user (401)
- [ ] Rate limiting after 5 failed attempts (429)
- [ ] Access token contains user ID and role
- [ ] Refresh token rotation works
- [ ] Old refresh token becomes invalid after rotation
- [ ] Logout revokes refresh token
- [ ] Logout-all revokes all sessions

### Password Security

- [ ] Password must be 8+ characters
- [ ] Password must contain uppercase
- [ ] Password must contain lowercase
- [ ] Password must contain digit
- [ ] Password change requires correct current password
- [ ] Password change revokes all other sessions
- [ ] Admin can reset any user's password

### Token Security

- [ ] Expired tokens are rejected
- [ ] Malformed tokens are rejected
- [ ] Tokens with wrong signature are rejected
- [ ] Tampered token payloads are rejected
- [ ] "none" algorithm tokens are rejected

### Role-Based Access Control

- [ ] Admin can access all endpoints
- [ ] Technician cannot access user management
- [ ] Support cannot access user management
- [ ] Readonly cannot access user management
- [ ] All roles can access /me
- [ ] All roles can change own password
- [ ] Users cannot change their own role
- [ ] Admin cannot deactivate themselves

## Error Codes Reference

| Error Code | HTTP Status | Description |
|------------|-------------|-------------|
| `AUTHENTICATION_ERROR` | 401 | Invalid credentials, expired token |
| `AUTHORIZATION_ERROR` | 403 | Insufficient permissions |
| `RATE_LIMIT_ERROR` | 429 | Too many failed attempts |
| `VALIDATION_ERROR` | 400 | Invalid input (weak password, etc.) |
| `CONFLICT` | 409 | Username/email already exists |
| `NOT_FOUND` | 404 | User not found |

## Troubleshooting

### "Invalid credentials" on correct password
1. Check username is lowercase (case-insensitive)
2. Verify user is active (`is_active: true`)
3. Check for rate limiting (try different IP or wait 15 min)

### "Token expired" immediately
1. Check server and client clock sync
2. Verify `JWT_EXPIRATION_MINUTES` in settings

### Rate limit hit accidentally
Rate limits clear after 15 minutes. Alternatively, restart the server to clear in-memory rate limit state.

### Cannot access admin endpoints
1. Verify user role is "admin"
2. Check token is valid and not expired
3. Verify Authorization header format: `Bearer <token>`

## Database Queries for Debugging

```sql
-- Check user details
SELECT id, username, email, role, is_active, last_login_at
FROM "user"
WHERE username = 'admin';

-- List active sessions for a user
SELECT id, device_info, ip_address, created_at, expires_at, is_revoked
FROM refresh_token
WHERE user_id = '<user-uuid>'
  AND is_revoked = false
  AND expires_at > NOW();

-- Count failed login attempts (in-memory, use logs)
-- Check application logs for "Failed login attempt" entries

-- Audit trail
-- Check audit logs for authentication events
```

## Performance Notes

| Operation | Typical Time |
|-----------|--------------|
| Login | 100-200ms (bcrypt is intentionally slow) |
| Token refresh | 10-50ms |
| Token validation | 1-5ms |
| Password hash | 100-150ms (bcrypt cost factor 12) |
