"""
API routes for managing notification subscriptions.

Security:
- Write endpoints (create, update, delete) require admin API key in production
- All inputs validated with strict Pydantic models
- Rate limited: 10 req/min for writes
- Email addresses validated
- Webhook URLs validated (format check)
- filter_config validated against key allowlist
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import Subscription
from app.security import limiter, require_admin_key
from app.validation import SubscriptionCreateRequest, SubscriptionUpdateRequest

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])


@router.get("/")
@limiter.limit("30/minute")
async def list_subscriptions(request: Request, db: AsyncSession = Depends(get_db)):
    """List all subscriptions."""
    result = await db.execute(
        select(Subscription).order_by(Subscription.created_at.desc()).limit(100)
    )
    subs = result.scalars().all()
    return {
        "count": len(subs),
        "subscriptions": [
            {
                "id": s.id,
                "name": s.name,
                # SECURITY: Partially mask email in list view
                "user_email": _mask_email(s.user_email),
                "filter_config": s.filter_config,
                "notification_method": s.notification_method,
                "active": s.active,
                "last_notified_at": s.last_notified_at.isoformat() if s.last_notified_at else None,
            }
            for s in subs
        ],
    }


@router.post("/", status_code=201, dependencies=[Depends(require_admin_key)])
@limiter.limit("10/minute")
async def create_subscription(
    request: Request,
    payload: SubscriptionCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new notification subscription.
    Requires X-API-Key header in production.
    """
    # Check subscription limit per email (prevent abuse)
    existing_count = await db.execute(
        select(Subscription)
        .where(Subscription.user_email == payload.user_email)
        .where(Subscription.active)
    )
    if len(existing_count.scalars().all()) >= 10:
        raise HTTPException(
            status_code=429,
            detail="Maximum 10 active subscriptions per email address."
        )

    sub = Subscription(
        name=payload.name,
        user_email=payload.user_email,
        filter_config=payload.filter_config,
        notification_method=payload.notification_method,
        webhook_url=payload.webhook_url,
        slack_channel=payload.slack_channel,
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)

    return {"id": sub.id, "name": sub.name, "status": "created"}


@router.patch("/{sub_id}", dependencies=[Depends(require_admin_key)])
@limiter.limit("10/minute")
async def update_subscription(
    request: Request,
    sub_id: int,
    payload: SubscriptionUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update a subscription. Requires X-API-Key in production."""
    if sub_id <= 0 or sub_id > 2147483647:
        raise HTTPException(status_code=400, detail="Invalid subscription ID")

    result = await db.execute(select(Subscription).where(Subscription.id == sub_id))
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    update_data = payload.model_dump(exclude_unset=True)
    if update_data:
        await db.execute(
            update(Subscription).where(Subscription.id == sub_id).values(**update_data)
        )
        await db.commit()

    return {"id": sub_id, "status": "updated"}


@router.delete("/{sub_id}", dependencies=[Depends(require_admin_key)])
@limiter.limit("10/minute")
async def delete_subscription(
    request: Request,
    sub_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a subscription. Requires X-API-Key in production."""
    if sub_id <= 0 or sub_id > 2147483647:
        raise HTTPException(status_code=400, detail="Invalid subscription ID")

    result = await db.execute(select(Subscription).where(Subscription.id == sub_id))
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    await db.execute(delete(Subscription).where(Subscription.id == sub_id))
    await db.commit()

    return {"id": sub_id, "status": "deleted"}


def _mask_email(email: str) -> str:
    """Partially mask email for privacy in list views. nam@example.com -> n**@example.com"""
    if not email or "@" not in email:
        return "***"
    local, domain = email.rsplit("@", 1)
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}{'*' * (len(local) - 1)}@{domain}"
