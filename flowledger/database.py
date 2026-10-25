"""Relational schema for durable workflow snapshots and ordered event history."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid


class Base(DeclarativeBase):
    pass


class CommandReceipt(Base):
    __tablename__ = "command_receipts"

    command_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    rejection: Mapped[str | None] = mapped_column(String(30))


class OutboxRecord(Base):
    __tablename__ = "command_outbox"

    command_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowRecord(Base):
    __tablename__ = "workflows"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="ck_workflows_attempts_nonnegative"),
        CheckConstraint("max_attempts BETWEEN 1 AND 100", name="ck_workflows_max_attempts_range"),
        CheckConstraint("attempts <= max_attempts", name="ck_workflows_attempts_within_limit"),
        CheckConstraint("version >= 0", name="ck_workflows_version_nonnegative"),
        CheckConstraint("length(owner_id) BETWEEN 1 AND 100 AND trim(owner_id) = owner_id",
                        name="ck_workflows_owner_id"),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_workflows_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    events: Mapped[list["EventRecord"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan", order_by="EventRecord.position"
    )


class EventRecord(Base):
    __tablename__ = "workflow_events"
    __table_args__ = (
        UniqueConstraint("workflow_id", "position", name="uq_workflow_events_position"),
        CheckConstraint("position >= 0", name="ck_workflow_events_position_nonnegative"),
        CheckConstraint("attempt >= 0", name="ck_workflow_events_attempt_nonnegative"),
        CheckConstraint("length(actor_id) BETWEEN 1 AND 100 AND trim(actor_id) = actor_id",
                        name="ck_workflow_events_actor_id"),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_workflow_events_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    workflow: Mapped[WorkflowRecord] = relationship(back_populates="events")

