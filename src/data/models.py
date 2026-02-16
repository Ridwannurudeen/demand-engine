from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

from src.config import DATABASE_URL


# ── Enums ────────────────────────────────────────────────────────────────


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    CLASSIFIED = "classified"
    QUOTED = "quoted"
    ACCEPTED = "accepted"
    MATCHING = "matching"
    ACP_CREATED = "acp_created"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskCategory(str, enum.Enum):
    CONTENT_CREATION = "content_creation"
    RESEARCH = "research"
    TRADING = "trading"
    CODE = "code"
    DESIGN = "design"
    DATA_ANALYSIS = "data_analysis"
    SOCIAL_MEDIA = "social_media"
    OTHER = "other"


# ── Base ─────────────────────────────────────────────────────────────────


class Base(DeclarativeBase):
    pass


# ── Models ───────────────────────────────────────────────────────────────


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Client info
    client_platform = Column(String(32), nullable=False)  # telegram, discord, etc.
    client_id = Column(String(128), nullable=False)  # platform-specific user id
    client_username = Column(String(128), nullable=True)

    # Request
    raw_request = Column(Text, nullable=False)
    category = Column(Enum(TaskCategory), nullable=True)
    complexity = Column(Float, nullable=True)  # 0.0 – 1.0
    requirements_json = Column(Text, nullable=True)  # JSON blob of extracted reqs

    # Pricing
    client_price = Column(Float, nullable=True)  # what we charge the client
    agent_price = Column(Float, nullable=True)  # what we pay the ACP agent
    margin = Column(Float, nullable=True)

    # ACP
    acp_job_id = Column(String(128), nullable=True)
    acp_agent_wallet = Column(String(128), nullable=True)
    acp_offering_id = Column(String(128), nullable=True)

    # Status
    status = Column(Enum(JobStatus), default=JobStatus.PENDING, nullable=False)
    result_summary = Column(Text, nullable=True)
    result_data = Column(Text, nullable=True)  # JSON blob or file path

    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    agent_scores = relationship("AgentScore", back_populates="job")


class Agent(Base):
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_address = Column(String(128), unique=True, nullable=False)
    name = Column(String(256), nullable=True)
    categories = Column(Text, nullable=True)  # JSON list of supported categories
    avg_quality = Column(Float, default=0.0)
    avg_speed = Column(Float, default=0.0)
    total_jobs = Column(Integer, default=0)
    success_rate = Column(Float, default=0.0)
    last_used = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    scores = relationship("AgentScore", back_populates="agent")


class AgentScore(Base):
    __tablename__ = "agent_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    quality_score = Column(Float, nullable=True)  # 0.0 – 1.0
    speed_score = Column(Float, nullable=True)  # 0.0 – 1.0
    communication_score = Column(Float, nullable=True)  # 0.0 – 1.0
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    job = relationship("Job", back_populates="agent_scores")
    agent = relationship("Agent", back_populates="scores")


class Revenue(Base):
    __tablename__ = "revenue"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    client_paid = Column(Float, nullable=False)
    agent_paid = Column(Float, nullable=False)
    protocol_fee = Column(Float, nullable=False)  # ACP 20% fee
    net_margin = Column(Float, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── Engine & Session ─────────────────────────────────────────────────────

async_engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
