"""
API v1 Router

Aggregates all v1 API endpoints:
- /admin - Admin dashboard endpoints
- /openpaygo - Device and metrics endpoints
- /webhooks - Payment webhook handlers
- /triggers - Payment trigger management
"""

from fastapi import APIRouter

from app.api.v1.admin import router as admin_router
from app.api.v1.openpaygo import router as openpaygo_router
from app.api.v1.triggers import router as triggers_router
from app.api.v1.webhooks import router as webhooks_router

router = APIRouter()

# Include all v1 routers
router.include_router(admin_router, prefix="/admin", tags=["Admin"])
router.include_router(openpaygo_router, prefix="/openpaygo", tags=["OpenPAYGO"])
router.include_router(webhooks_router, prefix="/webhooks", tags=["Webhooks"])
router.include_router(triggers_router, prefix="/triggers", tags=["Triggers"])
