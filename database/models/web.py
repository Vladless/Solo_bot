import uuid

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB

from ._base import Base, DictLikeMixin


class WebPage(DictLikeMixin, Base):
    __tablename__ = "web_pages"

    slug = Column(String(64), primary_key=True)
    title = Column(String(255), nullable=True)


class WebTheme(DictLikeMixin, Base):
    __tablename__ = "web_themes"

    page_slug = Column(String(64), ForeignKey("web_pages.slug", ondelete="CASCADE"), primary_key=True)
    tokens = Column(JSONB, nullable=False, default=dict)


class WebBlock(DictLikeMixin, Base):
    __tablename__ = "web_blocks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    page_slug = Column(String(64), ForeignKey("web_pages.slug", ondelete="CASCADE"), index=True, nullable=False)
    order = Column(Integer, nullable=False, default=0)
    type = Column(String(64), nullable=False)
    data = Column(JSONB, nullable=False, default=dict)


class WebPageVariant(DictLikeMixin, Base):
    __tablename__ = "web_page_variants"
    __table_args__ = (
        UniqueConstraint("page_slug", "variant_key", name="uq_web_page_variants_page_slug_variant_key"),
        Index("ix_web_page_variants_page_slug_is_active", "page_slug", "is_active"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    page_slug = Column(String(64), ForeignKey("web_pages.slug", ondelete="CASCADE"), index=True, nullable=False)
    variant_key = Column(String(64), nullable=False)
    name = Column(String(255), nullable=False, default="Default")
    is_active = Column(Boolean, nullable=False, server_default=sql_text("false"))
    theme_tokens = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WebPageVariantBlock(DictLikeMixin, Base):
    __tablename__ = "web_page_variant_blocks"
    __table_args__ = (Index("ix_web_page_variant_blocks_variant_id_order", "variant_id", "order"),)

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    variant_id = Column(String(36), ForeignKey("web_page_variants.id", ondelete="CASCADE"), index=True, nullable=False)
    order = Column(Integer, nullable=False, default=0)
    type = Column(String(64), nullable=False)
    data = Column(JSONB, nullable=False, default=dict)


class WebPushSubscription(DictLikeMixin, Base):
    __tablename__ = "web_push_subscriptions"
    __table_args__ = (Index("ix_web_push_subscriptions_user_id", "user_id"),)

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(BigInteger, nullable=False)
    identity_id = Column(String(36), nullable=True, index=True)
    endpoint = Column(Text, nullable=False, unique=True)
    keys_json = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class WebNotification(DictLikeMixin, Base):
    __tablename__ = "web_notifications"
    __table_args__ = (
        Index("ix_web_notifications_user_read", "user_id", "read"),
        Index("ix_web_notifications_created", "created_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(BigInteger, nullable=False, index=True)
    identity_id = Column(String(36), nullable=True, index=True)
    type = Column(String(32), nullable=False, default="system")
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False, default="")
    read = Column(Boolean, nullable=False, default=False)
    data = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class WebErrorReport(DictLikeMixin, Base):
    __tablename__ = "web_error_reports"
    __table_args__ = (
        Index("ix_web_error_reports_signature", "signature"),
        Index("ix_web_error_reports_resolved_last", "resolved", "last_seen_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    signature = Column(String(128), nullable=False, unique=True)
    error_name = Column(String(255), nullable=False, default="", server_default=sql_text("''"))
    error_message = Column(Text, nullable=False, default="", server_default=sql_text("''"))
    stack = Column(Text, nullable=True)
    url = Column(Text, nullable=True)
    user_agent = Column(Text, nullable=True)
    tag = Column(String(64), nullable=True)
    last_identity_id = Column(String(36), nullable=True)
    last_context = Column(JSONB, nullable=True)
    count = Column(Integer, nullable=False, default=1, server_default=sql_text("1"))
    resolved = Column(Boolean, nullable=False, default=False, server_default=sql_text("false"))
    first_seen_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=sql_text("CURRENT_TIMESTAMP"),
    )
    last_seen_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=sql_text("CURRENT_TIMESTAMP"),
    )


class WebFlowEvent(DictLikeMixin, Base):
    __tablename__ = "web_flow_events"
    __table_args__ = (
        Index("ix_web_flow_events_flow_node", "flow_id", "node_id"),
        Index("ix_web_flow_events_created", "created_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    flow_id = Column(String(64), nullable=False)
    node_id = Column(String(64), nullable=False)
    node_type = Column(String(32), nullable=False, default="", server_default=sql_text("''"))
    event_type = Column(String(32), nullable=False)
    ab_variant = Column(String(16), nullable=True)
    device = Column(String(16), nullable=True)
    locale = Column(String(8), nullable=True)
    authenticated = Column(Boolean, nullable=True)
    event_metadata = Column("metadata", JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=sql_text("CURRENT_TIMESTAMP"),
    )


class WebPageView(DictLikeMixin, Base):
    __tablename__ = "web_page_views"
    __table_args__ = (
        Index("ix_web_page_views_created", "created_at"),
        Index("ix_web_page_views_slug_created", "page_slug", "created_at"),
        Index("ix_web_page_views_visitor", "visitor_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    visitor_id = Column(String(36), nullable=False)
    page_slug = Column(String(64), nullable=False)
    referrer = Column(String(255), nullable=True)
    utm_source = Column(String(64), nullable=True)
    utm_medium = Column(String(64), nullable=True)
    utm_campaign = Column(String(64), nullable=True)
    device = Column(String(16), nullable=True)
    locale = Column(String(8), nullable=True)
    authenticated = Column(Boolean, nullable=True)
    source = Column(String(16), nullable=True)
    ab_variant = Column(String(16), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=sql_text("CURRENT_TIMESTAMP"),
    )


class WebCustomElementBuild(DictLikeMixin, Base):
    __tablename__ = "web_custom_element_builds"
    __table_args__ = (
        Index("ix_web_custom_element_builds_status", "status"),
        Index("ix_web_custom_element_builds_created", "created_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    label = Column(String(255), nullable=False, default="", server_default=sql_text("''"))
    slug = Column(String(128), nullable=False, default="", server_default=sql_text("''"))
    runtime = Column(
        String(32), nullable=False, default="react-component", server_default=sql_text("'react-component'")
    )
    source_kind = Column(String(32), nullable=False, default="inline-code", server_default=sql_text("'inline-code'"))
    source_value = Column(Text, nullable=False, default="", server_default=sql_text("''"))
    export_name = Column(String(128), nullable=False, default="default", server_default=sql_text("'default'"))
    props_schema_text = Column(Text, nullable=False, default="", server_default=sql_text("''"))
    sample_props_text = Column(Text, nullable=False, default="", server_default=sql_text("''"))
    events_text = Column(Text, nullable=False, default="", server_default=sql_text("''"))
    notes = Column(Text, nullable=False, default="", server_default=sql_text("''"))
    status = Column(String(32), nullable=False, default="queued", server_default=sql_text("'queued'"))
    summary = Column(Text, nullable=False, default="", server_default=sql_text("''"))
    next_steps = Column(JSONB, nullable=False, default=list, server_default=sql_text("'[]'::jsonb"))
    artifact = Column(JSONB, nullable=True)
    upload_meta = Column(JSONB, nullable=True)
    worker_id = Column(String(64), nullable=True)
    worker_claimed_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=sql_text("CURRENT_TIMESTAMP"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=sql_text("CURRENT_TIMESTAMP"),
    )


class WebFlow(DictLikeMixin, Base):
    __tablename__ = "web_flows"

    id = Column(String(64), primary_key=True, default="default")
    name = Column(String(255), nullable=False, default="Основной flow")
    nodes = Column(JSONB, nullable=False, default=list)
    edges = Column(JSONB, nullable=False, default=list)
    entry_node_id = Column(String(64), nullable=True)
    version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
