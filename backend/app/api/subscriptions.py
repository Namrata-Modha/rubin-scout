"""API routes for managing notification subscriptions."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import Subscription

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])


class SubscriptionCreate(BaseModel):
    name: str
    user_email: str
    filter_config: dict = {}
    notification_method: str = "email"
    webhook_url: str | None = None
    slack_channel: str | None = None

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Nearby bright supernovae",
                "user_email": "nam@example.com",
                "filter_config": {
                    "classification": ["SNIa", "SNII"],
                    "min_probability": 0.8,
                    "exclude_known_variables": True,
                },
                "notification_method": "slack",
                "webhook_url": "https://hooks.slack.com/services/...",
            }
        }


class SubscriptionUpdate(BaseModel):
    name: str | None = None
    filter_config: dict | None = None
    notification_method: str | None = None
    webhook_url: str | None = None
    active: bool | None = None


@router.get("/")
async def list_subscriptions(db: AsyncSession = Depends(get_db)):
    """List all subscriptions."""
    result = await db.execute(select(Subscription).order_by(Subscription.created_at.desc()))
    subs = result.scalars().all()
    return {
        "count": len(subs),
        "subscriptions": [
            {
                "id": s.id,
                "name": s.name,
                "user_email": s.user_email,
                "filter_config": s.filter_config,
                "notification_method": s.notification_method,
                "active": s.active,
                "last_notified_at": s.last_notified_at.isoformat() if s.last_notified_at else None,
            }
            for s in subs
        ],
    }


@router.post("/", status_code=201)
async def create_subscription(payload: SubscriptionCreate, db: AsyncSession = Depends(get_db)):
    """Create a new notification subscription."""
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


@router.patch("/{sub_id}")
async def update_subscription(
    sub_id: int, payload: SubscriptionUpdate, db: AsyncSession = Depends(get_db)
):
    """Update an existing subscription."""
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


@router.delete("/{sub_id}")
async def delete_subscription(sub_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a subscription."""
    result = await db.execute(select(Subscription).where(Subscription.id == sub_id))
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    await db.execute(delete(Subscription).where(Subscription.id == sub_id))
    await db.commit()

    return {"id": sub_id, "status": "deleted"}
