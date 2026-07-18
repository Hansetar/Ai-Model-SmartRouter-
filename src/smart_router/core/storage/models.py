"""SQLAlchemy ORM models - all database tables."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


class RequestLog(Base):
    """Request routing log."""

    __tablename__ = "request_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(Float, nullable=False, index=True)
    prompt_hash = Column(String(64), nullable=False)
    predicted_difficulty = Column(Integer)
    actual_difficulty = Column(Integer)
    routed_model = Column(String(255), index=True)
    cost = Column(Float, default=0.0)
    cost_currency = Column(String(10), default="CNY")
    latency_ms = Column(Integer, default=0)
    success = Column(Boolean, default=True)
    task_type = Column(String(50), index=True)
    route_source = Column(String(50))
    prompt_preview = Column(Text)
    requested_model = Column(String(255))
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    route_chain = Column(Text)
    content_types = Column(String(255))
    tenant_id = Column(String(100), index=True)
    modalities = Column(String(255), nullable=True, comment="Request modalities (JSON string)")
    tags = Column(String(255), nullable=True, comment="Request tags (JSON string)")

    __table_args__ = (
        Index("idx_request_logs_ts", "timestamp"),
        Index("idx_request_logs_model", "routed_model"),
        Index("idx_request_logs_task", "task_type"),
        Index("idx_request_logs_tenant", "tenant_id"),
    )


class ModelMetric(Base):
    """Aggregated model performance metrics."""

    __tablename__ = "model_metrics"

    model_name = Column(String(255), primary_key=True)
    success_rate = Column(Float, default=0.9)
    satisfaction_rate = Column(Float, default=0.9)
    total_calls = Column(Integer, default=0)
    success_calls = Column(Integer, default=0)
    positive_feedback = Column(Integer, default=0)
    negative_feedback = Column(Integer, default=0)
    last_balance = Column(Float)
    last_sync_time = Column(Float)
    health_status = Column(String(20), default="healthy")  # healthy, degraded, unhealthy
    consecutive_failures = Column(Integer, default=0)
    consecutive_successes = Column(Integer, default=0)
    last_health_check = Column(Float)


class FeedbackRecord(Base):
    """User feedback records."""

    __tablename__ = "feedback_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(64))
    feedback_type = Column(String(20))  # explicit / implicit
    sentiment = Column(String(20))  # positive / negative
    context_snapshot = Column(Text)
    timestamp = Column(Float)
    tenant_id = Column(String(100))


class TaskTypeStat(Base):
    """Task type statistics for adaptive learning."""

    __tablename__ = "task_type_stats"

    task_type = Column(String(50), primary_key=True)
    total_count = Column(Integer, default=0)
    positive_count = Column(Integer, default=0)
    negative_count = Column(Integer, default=0)


class TrainingSample(Base):
    """Training samples for ML predictor."""

    __tablename__ = "training_samples"

    id = Column(Integer, primary_key=True, autoincrement=True)
    prompt = Column(Text, nullable=False)
    difficulty = Column(Integer, nullable=False)
    est_tokens = Column(Integer, default=500)
    task_type = Column(String(50))
    model_name = Column(String(255))
    source = Column(String(50), default="auto", index=True)
    is_new = Column(Boolean, default=True)
    new_mark_ttl = Column(Float, default=3600)
    created_at = Column(Float)
    updated_at = Column(Float)


class ApiLog(Base):
    """API call logs for monitoring."""

    __tablename__ = "api_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(Float, nullable=False, index=True)
    request_id = Column(String(64))
    method = Column(String(10))
    path = Column(String(500))
    requested_model = Column(String(255))
    routed_model = Column(String(255), index=True)
    route_source = Column(String(50))
    status_code = Column(Integer, index=True)
    error_message = Column(Text)
    latency_ms = Column(Integer, default=0)
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    cost = Column(Float, default=0.0)
    cost_currency = Column(String(10), default="CNY")
    prompt_preview = Column(Text)
    client_ip = Column(String(50))
    tenant_id = Column(String(100))


class TenantUsage(Base):
    """Per-tenant usage tracking for quota enforcement."""

    __tablename__ = "tenant_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(100), nullable=False, index=True)
    date = Column(String(10), nullable=False)  # YYYY-MM-DD
    total_requests = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    total_input_tokens = Column(Integer, default=0)
    total_output_tokens = Column(Integer, default=0)
    total_cost = Column(Float, default=0.0)
    cost_currency = Column(String(10), default="CNY")

    __table_args__ = (
        Index("idx_tenant_usage_unique", "tenant_id", "date", unique=True),
    )


class ProviderBalanceLog(Base):
    """Provider balance history log."""

    __tablename__ = "provider_balance_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_name = Column(String(255), nullable=False, index=True)
    balance = Column(Float, nullable=True)
    currency = Column(String(10), default="USD")
    timestamp = Column(Float, nullable=False, index=True)

    __table_args__ = (
        Index("idx_provider_balance_logs_provider", "provider_name"),
        Index("idx_provider_balance_logs_ts", "timestamp"),
    )


class TenantBalance(Base):
    """Tenant balance record (persisted, replaces in-memory dict)."""

    __tablename__ = "tenant_balances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(100), nullable=False, unique=True, index=True)
    balance = Column(Float, default=0, comment="Balance amount, -1=unlimited")
    currency = Column(String(10), default="CNY")
    unlimited = Column(Boolean, default=False, comment="If true, record usage but don't deduct")
    updated_at = Column(Float, comment="Last update timestamp")
