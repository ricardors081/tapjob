"""Messaging & channels domain (WhatsApp Alpha vertical slice foundation)."""

from tapjob_api.messaging.applications import (
    Application,
    ApplicationKit,
    ApplicationStatus,
    ApplyMode,
)
from tapjob_api.messaging.inbound import (
    ProviderEventEnvelope,
    ProviderEventProcessingStatus,
    ProviderEventType,
)
from tapjob_api.messaging.models import (
    ChannelIdentity,
    ChannelIdentityLink,
    ChannelIdentityLinkStatus,
    ChannelProvider,
    ChannelType,
)
from tapjob_api.messaging.outbox import (
    OutboundMessageQueue,
    OutboundMessageStatus,
)
from tapjob_api.messaging.reminders import (
    Reminder,
    ReminderStatus,
)

__all__ = [
    "Application",
    "ApplicationKit",
    "ApplicationStatus",
    "ApplyMode",
    "ChannelIdentity",
    "ChannelIdentityLink",
    "ChannelIdentityLinkStatus",
    "ChannelProvider",
    "ChannelType",
    "OutboundMessageQueue",
    "OutboundMessageStatus",
    "ProviderEventEnvelope",
    "ProviderEventProcessingStatus",
    "ProviderEventType",
    "Reminder",
    "ReminderStatus",
]
