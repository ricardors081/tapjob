"""SQLAlchemy ORM models for messaging channels (WhatsApp Alpha vertical slice).

Channel identity linking enables provider-neutral mapping of external messaging identities
(e.g. WhatsApp phone numbers, future Telegram user IDs) to canonical TapJob User accounts.

No business logic depends on channel identity structure, and no channel-specific secrets are
ever stored here — only the external identity and the TapJob user it maps to.

Every enum-shaped column uses ``sa.Enum(..., native_enum=False, validate_strings=True)`` -- a
`VARCHAR` plus a `CHECK` constraint, never a PostgreSQL native `ENUM` type
(docs/ENGINEERING_STANDARDS.md §23).
"""

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tapjob_api.db.base import Base
from tapjob_api.identity.models import _enum_column


class ChannelType(StrEnum):
    """Provider-neutral channel kind, never a provider-specific type.

    WhatsApp is a channel KIND (not Evolution/Baileys-specific); future migrations
    to WhatsApp Cloud API remain a capability difference, not an architectural change.
    """

    WHATSAPP = "whatsapp"


class ChannelIdentityLinkStatus(StrEnum):
    """Link lifecycle status."""

    PENDING = "pending"
    VERIFIED = "verified"
    BROKEN = "broken"


class ChannelProvider(Base):
    """A messaging provider instance (e.g. Evolution API instance, future WhatsApp Cloud account).

    Allows multi-tenant deployments and provider switching without altering user identity.
    """

    __tablename__ = "channel_providers"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    channel: Mapped[ChannelType] = mapped_column(
        _enum_column(ChannelType, name="channel_type", length=32),
        nullable=False,
    )
    provider_name: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    provider_config_key: Mapped[str] = mapped_column(sa.String(128), nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    channel_identities: Mapped[list["ChannelIdentity"]] = relationship(
        back_populates="provider", cascade="all, delete-orphan"
    )


class ChannelIdentity(Base):
    """A provider-issued external identity (e.g. WhatsApp phone number, Telegram user ID).

    Provider-neutral: the `external_id` field is opaque to this layer and must never
    assume a shape (e.g. E.164 phone format, Baileys JID). A future WhatsApp Cloud API
    adapter will supply a different shape; the schema adapts via this same table.

    Unique constraint ensures each (provider, external_id) pair is singular within a provider
    instance, supporting safe idempotent linking and deduplication.
    """

    __tablename__ = "channel_identities"
    __table_args__ = (
        sa.UniqueConstraint(
            "provider_id", "external_id", name="uq_channel_identities_provider_external"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("channel_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_id: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    provider: Mapped["ChannelProvider"] = relationship(back_populates="channel_identities")
    links: Mapped[list["ChannelIdentityLink"]] = relationship(
        back_populates="channel_identity", cascade="all, delete-orphan"
    )


class ChannelIdentityLink(Base):
    """Maps a ChannelIdentity to a canonical TapJob User.

    Supports one-to-many (same external_id can link to one user, but user lookup must
    always resolve to exactly one link per (user, channel_identity) pair via the unique
    constraint).

    Link is idempotent: relinking the same external_id to the same user is a no-op.
    Relinking to a different user is forbidden (use BROKEN status to transition).
    """

    __tablename__ = "channel_identity_links"
    __table_args__ = (
        sa.UniqueConstraint(
            "channel_identity_id", "user_id", name="uq_channel_identity_links_identity_user"
        ),
        sa.Index(
            "uq_channel_identity_links_single_active",
            "channel_identity_id",
            unique=True,
            postgresql_where=sa.text("status != 'broken'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    channel_identity_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("channel_identities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[ChannelIdentityLinkStatus] = mapped_column(
        _enum_column(ChannelIdentityLinkStatus, name="channel_identity_link_status", length=16),
        nullable=False,
        default=ChannelIdentityLinkStatus.PENDING,
        server_default=ChannelIdentityLinkStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    verified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    channel_identity: Mapped["ChannelIdentity"] = relationship(back_populates="links")
