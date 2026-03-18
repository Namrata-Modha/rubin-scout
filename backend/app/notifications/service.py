"""
Notification Service.

Sends alerts through Slack webhooks, email, or generic webhooks
when new transients match a subscription's filter criteria.
"""

import json
import logging
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.models import Object, Subscription

logger = logging.getLogger(__name__)
settings = get_settings()


class NotificationService:
    """Evaluates subscriptions and dispatches notifications."""

    async def check_and_notify(self, session: AsyncSession, new_objects: list[Object]):
        """
        For each active subscription, check if any new objects match
        the filter criteria. Send notifications for matches.
        """
        if not new_objects:
            return

        result = await session.execute(
            select(Subscription).where(Subscription.active == True)
        )
        subscriptions = result.scalars().all()

        for sub in subscriptions:
            matching = self._filter_objects(new_objects, sub.filter_config)
            if not matching:
                continue

            try:
                await self._dispatch(sub, matching)

                await session.execute(
                    update(Subscription)
                    .where(Subscription.id == sub.id)
                    .values(last_notified_at=datetime.now(timezone.utc))
                )
                logger.info(
                    f"Notified subscription '{sub.name}' ({sub.notification_method}) "
                    f"with {len(matching)} alerts"
                )
            except Exception as e:
                logger.error(f"Notification failed for subscription {sub.id}: {e}")

        await session.commit()

    def _filter_objects(self, objects: list[Object], filter_config: dict) -> list[Object]:
        """Apply a subscription's filter config to a list of objects."""
        matched = []

        classes = filter_config.get("classification")
        min_prob = filter_config.get("min_probability", 0)
        max_mag = filter_config.get("max_magnitude")
        exclude_known = filter_config.get("exclude_known_variables", False)

        for obj in objects:
            # Classification filter
            if classes and obj.classification not in classes:
                continue

            # Probability filter
            if obj.classification_probability and obj.classification_probability < min_prob:
                continue

            # Exclude known variables (has a SIMBAD cross-match that's a variable star)
            if exclude_known and obj.cross_match_type and "V*" in obj.cross_match_type:
                continue

            matched.append(obj)

        return matched

    async def _dispatch(self, sub: Subscription, objects: list[Object]):
        """Route notification to the right channel."""
        method = sub.notification_method

        if method == "slack" and (sub.webhook_url or settings.slack_webhook_url):
            await self._send_slack(
                sub.webhook_url or settings.slack_webhook_url, sub.name, objects
            )
        elif method == "email" and sub.user_email:
            self._send_email(sub.user_email, sub.name, objects)
        elif method == "webhook" and sub.webhook_url:
            await self._send_webhook(sub.webhook_url, sub.name, objects)
        else:
            logger.warning(f"No valid delivery method for subscription {sub.id}")

    async def _send_slack(self, webhook_url: str, sub_name: str, objects: list[Object]):
        """Send a Slack notification with alert summaries."""
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Rubin Scout: {len(objects)} new alert(s)",
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Subscription: *{sub_name}*",
                    }
                ],
            },
            {"type": "divider"},
        ]

        for obj in objects[:10]:  # Cap at 10 to avoid huge messages
            prob_pct = f"{obj.classification_probability * 100:.0f}%" if obj.classification_probability else "N/A"
            alerce_url = f"https://alerce.online/object/{obj.oid}"

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*<{alerce_url}|{obj.oid}>* — {obj.classification} ({prob_pct})\n"
                        f"RA: `{obj.ra:.4f}` Dec: `{obj.dec:.4f}` | "
                        f"{obj.n_detections} detections"
                        + (f" | Near: {obj.cross_match_name}" if obj.cross_match_name else "")
                    ),
                },
            })

        if len(objects) > 10:
            blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"_...and {len(objects) - 10} more_"}
                ],
            })

        payload = {"blocks": blocks}

        async with httpx.AsyncClient() as client:
            response = await client.post(webhook_url, json=payload, timeout=10)
            response.raise_for_status()

    def _send_email(self, to_email: str, sub_name: str, objects: list[Object]):
        """Send an email digest of new alerts."""
        if not settings.smtp_host:
            logger.warning("SMTP not configured, skipping email notification")
            return

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Rubin Scout: {len(objects)} new transient alert(s)"
        msg["From"] = settings.notification_from_email
        msg["To"] = to_email

        # Plain text version
        lines = [
            f"Rubin Scout Alert — Subscription: {sub_name}",
            f"{len(objects)} new object(s) matched your filters.\n",
        ]
        for obj in objects[:20]:
            prob_pct = f"{obj.classification_probability * 100:.0f}%" if obj.classification_probability else "?"
            lines.append(
                f"  {obj.oid}  {obj.classification} ({prob_pct})  "
                f"RA={obj.ra:.4f} Dec={obj.dec:.4f}  "
                f"https://alerce.online/object/{obj.oid}"
            )

        lines.append(f"\nView all alerts on your Rubin Scout dashboard.")
        text_body = "\n".join(lines)
        msg.attach(MIMEText(text_body, "plain"))

        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
                server.starttls()
                if settings.smtp_user:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(msg)
        except Exception as e:
            logger.error(f"Email send failed: {e}")
            raise

    async def _send_webhook(self, url: str, sub_name: str, objects: list[Object]):
        """Send a generic JSON webhook with alert data."""
        payload = {
            "subscription": sub_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "count": len(objects),
            "alerts": [
                {
                    "oid": obj.oid,
                    "ra": obj.ra,
                    "dec": obj.dec,
                    "classification": obj.classification,
                    "probability": obj.classification_probability,
                    "n_detections": obj.n_detections,
                    "last_detection": obj.last_detection.isoformat() if obj.last_detection else None,
                    "cross_match": obj.cross_match_name,
                    "alerce_url": f"https://alerce.online/object/{obj.oid}",
                }
                for obj in objects
            ],
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=10)
            response.raise_for_status()
