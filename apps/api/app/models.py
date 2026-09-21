import uuid
from decimal import Decimal
from datetime import datetime, timedelta, timezone

from sqlalchemy import event, inspect, select, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, JSON, LargeBinary, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def new_public_id() -> str:
    return uuid.uuid4().hex


def new_domain_token() -> str:
    return uuid.uuid4().hex


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Agency(Base):
    __tablename__ = "agencies"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(180))
    slug: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    brand_color: Mapped[str] = mapped_column(String(20), default="#075985")
    logo_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    logo_mime: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    users: Mapped[list["User"]] = relationship(back_populates="agency", cascade="all, delete-orphan")

    @property
    def logo_url(self) -> str | None:
        return f"/api/agency/logo?v={int(self.created_at.timestamp())}" if self.logo_data else None


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    agency_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agencies.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(320), index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(30), default="admin")
    is_vendiq_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    password_recovery_code_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_recovery_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_recovery_last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_recovery_send_window_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_recovery_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    password_recovery_send_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    password_recovery_credentials_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    agency: Mapped[Agency] = relationship(back_populates="users")


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    agency_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agencies.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(180), index=True)
    industry: Mapped[str] = mapped_column(String(160), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    general_context: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    portal_slug: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    portal_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    portal_title: Mapped[str] = mapped_column(String(180), default="")
    portal_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    portal_password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    portal_email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    portal_verification_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    portal_verification_last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    portal_verification_send_window_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    portal_verification_code_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    portal_verification_attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    portal_verification_send_count: Mapped[int] = mapped_column(default=0, server_default="0")
    portal_credentials_version: Mapped[int] = mapped_column(default=0, server_default="0")
    # Optional custom domain for this client's portal. Verified via a DNS TXT
    # challenge; only verified domains are routed and get an on-demand cert.
    portal_domain: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    portal_domain_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    portal_domain_token: Mapped[str] = mapped_column(String(64), default="", server_default="")
    sales_advisor_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    google_calendar_refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_calendar_id: Mapped[str] = mapped_column(String(255), default="primary", server_default="primary")
    google_calendar_timezone: Mapped[str] = mapped_column(String(64), default="America/Lima", server_default="America/Lima")
    calendar_workday_start: Mapped[str] = mapped_column(String(5), default="09:00", server_default="09:00")
    calendar_workday_end: Mapped[str] = mapped_column(String(5), default="18:00", server_default="18:00")
    calendar_working_days: Mapped[str] = mapped_column(String(20), default="0,1,2,3,4,5", server_default="0,1,2,3,4,5")
    calendar_slot_minutes: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    calendar_buffer_minutes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    calendar_min_notice_minutes: Mapped[int] = mapped_column(Integer, default=60, server_default="60")
    calendar_booking_horizon_days: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    restaurant_currency: Mapped[str] = mapped_column(String(3), default="PEN", server_default="PEN")
    restaurant_payment_instructions: Mapped[str] = mapped_column(Text, default="", server_default="")
    restaurant_kitchen_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    restaurant_delivery_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    agents: Mapped[list["Agent"]] = relationship(back_populates="client", cascade="all, delete-orphan")
    leads: Mapped[list["Lead"]] = relationship(back_populates="client", cascade="all, delete-orphan")
    lead_handoffs: Mapped[list["LeadHandoff"]] = relationship(back_populates="client", cascade="all, delete-orphan")
    whatsapp_channel: Mapped["WhatsAppChannel | None"] = relationship(
        back_populates="client", cascade="all, delete-orphan", uselist=False
    )
    whatsapp_cloud_channel: Mapped["WhatsAppCloudChannel | None"] = relationship(
        back_populates="client", cascade="all, delete-orphan", uselist=False
    )

    @property
    def portal_password_configured(self) -> bool:
        return bool(self.portal_password_hash)


class ProviderCredential(Base):
    """One AI provider API key per agency (bring your own key). provider is
    "openai" or "anthropic"; the base URL is resolved from the provider."""

    __tablename__ = "provider_credentials"
    __table_args__ = (UniqueConstraint("agency_id", "provider", name="uq_provider_credentials_agency_provider"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    agency_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agencies.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    agency_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agencies.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(180))
    description: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    personality: Mapped[str] = mapped_column(Text, default="")
    # Structured business brief. Optional guided fields that compose into the
    # system prompt alongside the free-form instructions.
    brief_summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    brief_products: Mapped[str] = mapped_column(Text, default="", server_default="")
    brief_audience: Mapped[str] = mapped_column(Text, default="", server_default="")
    brief_policies: Mapped[str] = mapped_column(Text, default="", server_default="")
    brief_goal: Mapped[str] = mapped_column(Text, default="", server_default="")
    brief_dos: Mapped[str] = mapped_column(Text, default="", server_default="")
    brief_donts: Mapped[str] = mapped_column(Text, default="", server_default="")
    # AI provider ("openai" or "anthropic"); the agency's key for that provider is used.
    provider: Mapped[str] = mapped_column(String(30), default="openai", server_default="openai")
    model: Mapped[str] = mapped_column(String(180), default="")
    # IANA timezone (e.g. "America/Bogota"); injected into the system prompt so
    # the agent knows the local date/time. "UTC" when unset.
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    manual_context: Mapped[str] = mapped_column(Text, default="")
    # Generation settings. Sampling params are applied best-effort by the AI
    # service (models that reject them fall back to their defaults).
    temperature: Mapped[float] = mapped_column(Float, default=0.7, server_default="0.7")
    max_tokens: Mapped[int] = mapped_column(Integer, default=2048, server_default="2048")
    # How many past messages are kept as conversation memory.
    memory_limit: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    # Multimodal capabilities. When enabled, inbound images are described by a
    # vision model and inbound audio is transcribed before reaching the agent.
    image_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    image_model: Mapped[str] = mapped_column(String(180), default="", server_default="")
    audio_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    audio_model: Mapped[str] = mapped_column(String(180), default="whisper-1", server_default="whisper-1")
    # Embeddable web chat widget.
    widget_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    widget_public_id: Mapped[str] = mapped_column(String(64), default=new_public_id, unique=True, index=True)
    widget_greeting: Mapped[str] = mapped_column(Text, default="", server_default="")
    widget_color: Mapped[str] = mapped_column(String(20), default="", server_default="")
    widget_position: Mapped[str] = mapped_column(String(10), default="right", server_default="right")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    client: Mapped[Client] = relationship(back_populates="agents")
    documents: Mapped[list["KnowledgeDocument"]] = relationship(back_populates="agent", cascade="all, delete-orphan")
    qa_pairs: Mapped[list["AgentQA"]] = relationship(back_populates="agent", cascade="all, delete-orphan", order_by="AgentQA.position")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="agent", cascade="all, delete-orphan")
    whatsapp_channels: Mapped[list["WhatsAppChannel"]] = relationship(back_populates="agent")
    whatsapp_cloud_channels: Mapped[list["WhatsAppCloudChannel"]] = relationship(back_populates="agent")
    tools: Mapped[list["AgentTool"]] = relationship(back_populates="agent", cascade="all, delete-orphan", order_by="AgentTool.created_at")
    leads: Mapped[list["Lead"]] = relationship(back_populates="agent")


class AgentTool(Base):
    """A custom tool the agent can call: a user-defined HTTP endpoint
    ("http") or an external MCP server ("mcp")."""

    __tablename__ = "agent_tools"
    __table_args__ = (UniqueConstraint("agent_id", "name", name="uq_agent_tools_agent_name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # HTTP endpoint (may contain {param} path placeholders) or MCP server URL.
    url: Mapped[str] = mapped_column(Text, default="")
    # HTTP tools only.
    http_method: Mapped[str] = mapped_column(String(10), default="GET")
    prompt_instructions: Mapped[str] = mapped_column(Text, default="")
    body_params: Mapped[list] = mapped_column(JSON, default=list)
    query_params: Mapped[list] = mapped_column(JSON, default=list)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30)
    # MCP servers only. cached_tools holds the last list_tools result so chat
    # requests never block on discovery; refreshed on save/test-connection.
    transport: Mapped[str] = mapped_column(String(20), default="streamable_http")
    cached_tools: Mapped[list] = mapped_column(JSON, default=list)
    tools_cached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The full auth headers dict, encrypted at rest; never returned by the API.
    encrypted_headers: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    agent: Mapped[Agent] = relationship(back_populates="tools")


class WhatsAppChannel(Base):
    __tablename__ = "whatsapp_channels"
    __table_args__ = (UniqueConstraint("client_id", name="uq_whatsapp_channels_client_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    agency_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agencies.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="disconnected")
    phone_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    encrypted_auth_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_qr: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    client: Mapped[Client] = relationship(back_populates="whatsapp_channel")
    agent: Mapped[Agent] = relationship(back_populates="whatsapp_channels")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="whatsapp_channel")


class WhatsAppCloudChannel(Base):
    """Official WhatsApp Business Cloud API channel (Meta Graph API). Coexists
    with the Baileys channel: a client can have one of each, on different
    numbers. Credentials are provided manually (bring your own Meta app)."""

    __tablename__ = "whatsapp_cloud_channels"
    __table_args__ = (UniqueConstraint("client_id", name="uq_whatsapp_cloud_channels_client_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    agency_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agencies.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="disconnected")
    phone_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    phone_number_id: Mapped[str] = mapped_column(String(80), default="", server_default="")
    waba_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    encrypted_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_app_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Token the owner pastes into their Meta app's webhook config; it must be
    # re-displayable, so it is stored in plain text like portal_domain_token.
    webhook_verify_token: Mapped[str] = mapped_column(String(64), default=new_public_id, server_default="")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    client: Mapped[Client] = relationship(back_populates="whatsapp_cloud_channel")
    agent: Mapped[Agent] = relationship(back_populates="whatsapp_cloud_channels")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="whatsapp_cloud_channel")


class AgentQA(Base):
    __tablename__ = "agent_qa"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    agent: Mapped[Agent] = relationship(back_populates="qa_pairs")


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_data: Mapped[bytes] = mapped_column(LargeBinary)
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="processed")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    agent: Mapped[Agent] = relationship(back_populates="documents")
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text)
    # Embedding vector stored as a JSON array of floats (portable across any
    # Postgres; similarity is computed in Python). Swap to pgvector at scale.
    embedding: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    agency_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agencies.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(180))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("whatsapp_channel_id", "external_chat_id", name="uq_conversations_whatsapp_chat"),
        UniqueConstraint(
            "whatsapp_cloud_channel_id", "external_chat_id", name="uq_conversations_whatsapp_cloud_chat"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    agency_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agencies.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(240), default="New conversation")
    mode: Mapped[str] = mapped_column(String(30), default="ai")
    channel: Mapped[str] = mapped_column(String(40), default="playground")
    whatsapp_channel_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("whatsapp_channels.id", ondelete="CASCADE"), nullable=True, index=True
    )
    whatsapp_cloud_channel_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("whatsapp_cloud_channels.id", ondelete="CASCADE"), nullable=True, index=True
    )
    external_chat_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    operator_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    agent: Mapped[Agent] = relationship(back_populates="conversations")
    whatsapp_channel: Mapped[WhatsAppChannel | None] = relationship(back_populates="conversations")
    whatsapp_cloud_channel: Mapped[WhatsAppCloudChannel | None] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at")
    lead_link: Mapped["LeadConversation | None"] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", uselist=False
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "external_message_id", name="uq_messages_conversation_external"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    # Tool usage behind an assistant reply: [{name, arguments, result_preview, is_error}].
    tool_calls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    sender_type: Mapped[str] = mapped_column(String(30), default="visitor")
    sender_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        CheckConstraint(
            "status IN ('new', 'qualified', 'follow_up', 'won', 'lost')",
            name="ck_leads_status",
        ),
        UniqueConstraint("agency_id", "client_id", "phone_normalized", name="uq_leads_tenant_phone"),
        UniqueConstraint("agency_id", "client_id", "email_normalized", name="uq_leads_tenant_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    agency_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agencies.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    phone_normalized: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    email_normalized: Mapped[str | None] = mapped_column(String(320), nullable=True)
    interest: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget: Mapped[str | None] = mapped_column(String(180), nullable=True)
    preferred_contact_time: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="playground", server_default="playground")
    status: Mapped[str] = mapped_column(String(30), default="new", server_default="new", index=True)
    next_follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    advisor_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    advisor_notification_external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    advisor_notification_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    client: Mapped[Client] = relationship(back_populates="leads")
    agent: Mapped[Agent] = relationship(back_populates="leads")
    conversations: Mapped[list["LeadConversation"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    handoffs: Mapped[list["LeadHandoff"]] = relationship(back_populates="lead", cascade="all, delete-orphan")


class LeadConversation(Base):
    __tablename__ = "lead_conversations"
    __table_args__ = (UniqueConstraint("conversation_id", name="uq_lead_conversations_conversation"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    lead: Mapped[Lead] = relationship(back_populates="conversations")
    conversation: Mapped[Conversation] = relationship(back_populates="lead_link")


class LeadHandoff(Base):
    __tablename__ = "lead_handoffs"
    __table_args__ = (
        CheckConstraint("status IN ('sending', 'sent', 'failed')", name="ck_lead_handoffs_status"),
        UniqueConstraint("consent_message_id", name="uq_lead_handoffs_consent_message"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    agency_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agencies.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    consent_message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    advisor_phone: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), default="sending", server_default="sending")
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    client: Mapped[Client] = relationship(back_populates="lead_handoffs")
    lead: Mapped[Lead] = relationship(back_populates="handoffs")


class CalendarAppointment(Base):
    __tablename__ = "calendar_appointments"
    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_calendar_appointments_conversation"),
        CheckConstraint("status IN ('confirmed', 'cancelled')", name="ck_calendar_appointments_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    agency_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agencies.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="RESTRICT"), index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    google_event_id: Mapped[str] = mapped_column(String(255))
    calendar_id: Mapped[str] = mapped_column(String(255), default="primary", server_default="primary")
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="confirmed", server_default="confirmed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class RestaurantMenuItem(Base):
    __tablename__ = "restaurant_menu_items"
    __table_args__ = (
        UniqueConstraint("client_id", "name", name="uq_restaurant_menu_item_client_name"),
        CheckConstraint("price >= 0", name="ck_restaurant_menu_item_price"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    agency_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agencies.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(180))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="PEN", server_default="PEN")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class RestaurantOrder(Base):
    __tablename__ = "restaurant_orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','awaiting_confirmation','awaiting_payment','payment_reported','paid','kitchen','ready','out_for_delivery','served','delivered','cancelled')",
            name="ck_restaurant_orders_status",
        ),
        CheckConstraint(
            "payment_status IN ('pending','reported','confirmed','rejected')",
            name="ck_restaurant_orders_payment_status",
        ),
        CheckConstraint("source IN ('whatsapp','table','playground')", name="ck_restaurant_orders_source"),
        CheckConstraint(
            "fulfillment_type IS NULL OR fulfillment_type IN ('delivery','table','pickup')",
            name="ck_restaurant_orders_fulfillment",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    public_code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    agency_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agencies.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="RESTRICT"), index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(20))
    table_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    fulfillment_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    delivery_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="draft", server_default="draft", index=True)
    payment_status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending", index=True)
    payment_method: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(180), nullable=True)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    currency: Mapped[str] = mapped_column(String(3), default="PEN", server_default="PEN")
    customer_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    kitchen_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class RestaurantOrderItem(Base):
    __tablename__ = "restaurant_order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_restaurant_order_items_quantity"),
        CheckConstraint("unit_price >= 0", name="ck_restaurant_order_items_unit_price"),
        CheckConstraint("line_total >= 0", name="ck_restaurant_order_items_line_total"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant_orders.id", ondelete="CASCADE"), index=True)
    menu_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant_menu_items.id", ondelete="RESTRICT"), index=True)
    item_name: Mapped[str] = mapped_column(String(180))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    quantity: Mapped[int] = mapped_column(Integer)
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class Plan(Base):
    """VENDIQ subscription offering; no business/customer payment data."""
    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint("monthly_price IS NULL OR monthly_price >= 0", name="ck_plans_monthly_price"),
        CheckConstraint("length(currency) = 3 AND currency = upper(currency)", name="ck_plans_currency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(180))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    monthly_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    available_for_new_subscriptions: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    modules: Mapped[list["Module"]] = relationship(secondary="plan_modules", order_by="Module.code")


class Module(Base):
    __tablename__ = "modules"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(180))
    # Catalog inclusion does not imply that a feature has been implemented.
    is_available: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class PlanModule(Base):
    __tablename__ = "plan_modules"

    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True)
    module_code: Mapped[str] = mapped_column(ForeignKey("modules.code", ondelete="RESTRICT"), primary_key=True)


class ClientSubscription(Base):
    __tablename__ = "client_subscriptions"
    __table_args__ = (
        UniqueConstraint("client_id", name="uq_client_subscriptions_client_id"),
        CheckConstraint(
            "status IN ('TRIAL', 'ACTIVE', 'PAYMENT_PENDING', 'SUSPENDED', 'CANCELLED')",
            name="ck_client_subscriptions_status",
        ),
        CheckConstraint(
            "(first_activated_at IS NULL AND trial_started_at IS NULL AND trial_ends_at IS NULL) OR "
            "(first_activated_at IS NOT NULL AND trial_started_at IS NOT NULL AND trial_ends_at IS NOT NULL "
            "AND trial_started_at = first_activated_at AND trial_ends_at > trial_started_at)",
            name="ck_client_subscriptions_trial_dates",
        ),
        CheckConstraint(
            "trial_ends_at IS NULL OR trial_ends_at = trial_started_at + interval '72 hours'",
            name="ck_client_subscriptions_trial_72h",
        ).ddl_if(dialect="postgresql"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(20))
    first_activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_renewal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    client: Mapped[Client] = relationship()
    plan: Mapped[Plan] = relationship()


@event.listens_for(ClientSubscription, "before_insert")
@event.listens_for(ClientSubscription, "before_update")
def _validate_subscription_dates(mapper, connection, subscription: ClientSubscription) -> None:
    """Integrity validation, not an activation hook. PostgreSQL also guards writes."""
    dates = (subscription.first_activated_at, subscription.trial_started_at, subscription.trial_ends_at)
    if any(value is not None for value in dates):
        if any(value is None for value in dates):
            raise ValueError("Activation and trial dates must be set together")
        # SQLite fixtures reload datetimes without timezone; production uses timestamptz.
        normalized = tuple(value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value for value in dates)
        if normalized[0] != normalized[1] or normalized[2] - normalized[0] != timedelta(hours=72):
            raise ValueError("Trial must last exactly 72 hours from first activation")
    if inspect(subscription).persistent:
        previous = connection.execute(select(ClientSubscription.first_activated_at).where(
            ClientSubscription.id == subscription.id,
        )).scalar_one()
        if previous is not None:
            current = subscription.first_activated_at
            previous = previous.replace(tzinfo=timezone.utc) if previous.tzinfo is None else previous
            if current is not None and current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            if current != previous:
                raise ValueError("first_activated_at is immutable")


# Register isolated subscription economics metadata.
from . import models_promotions  # noqa: E402, F401

# Register transport evidence metadata only; no activation hooks.
from . import models_subscription_activation  # noqa: E402, F401
