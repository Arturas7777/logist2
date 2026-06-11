"""Реэкспорт: модуль перенесён в ``core/models/email.py`` (A1, AUDIT_ROUND3)."""

from core.models.email import *  # noqa: F403
from core.models.email import (  # noqa: F401
    ContainerEmail,
    ContainerEmailLink,
    EmailGroup,
    EmailGroupMember,
    EmailIngestFilter,
    GmailSyncState,
)
