"""Small, tenant-scoped audit events for security and member administration."""

import logging

from google.cloud import firestore

from app.config import AUDIT_EVENT_COLLECTION, ENTITY_COLLECTION, SECURITY_AUDIT_COLLECTION
from app.connections import get_firestore_client

logger = logging.getLogger(__name__)


def safe_actor(user):
    """Keep only the identity context needed to explain an administrative event."""
    return {
        "user_id": user.get("uid"),
        "display_name": user.get("display_name"),
        "email": user.get("email"),
        "role": user.get("role"),
    }


def record_audit_event(entity_id, event_type, actor, subject=None, metadata=None):
    """Append an event without credential values, documents, or session tokens."""
    event = {
        "event_type": event_type,
        "actor": safe_actor(actor),
        "subject": subject or {},
        "metadata": metadata or {},
        "recorded_at": firestore.SERVER_TIMESTAMP,
    }
    get_firestore_client().collection(ENTITY_COLLECTION).document(entity_id).collection(
        AUDIT_EVENT_COLLECTION
    ).document().set(event)


def record_security_event(event_type, actor, metadata=None):
    """Log denied requests outside the requested tenant to avoid cross-tenant writes."""
    get_firestore_client().collection(SECURITY_AUDIT_COLLECTION).document().set(
        {
            "event_type": event_type,
            "actor": safe_actor(actor),
            "metadata": metadata or {},
            "recorded_at": firestore.SERVER_TIMESTAMP,
        }
    )


def safely_record_audit_event(*args, **kwargs):
    """Audit availability must not block a permitted business operation."""
    try:
        record_audit_event(*args, **kwargs)
    except Exception:
        logger.exception("Could not record tenant audit event")


def safely_record_security_event(*args, **kwargs):
    """Keep authorization decisions intact even if audit storage is unavailable."""
    try:
        record_security_event(*args, **kwargs)
    except Exception:
        logger.exception("Could not record security audit event")
