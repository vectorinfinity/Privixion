from datetime import datetime, timezone
import uuid
from sqlalchemy import create_engine, String, Text, Integer, JSON, DateTime, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import settings


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True,
                       connect_args={'check_same_thread': False} if settings.database_url.startswith('sqlite') else {})
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Document(Base):
    __tablename__ = 'documents'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(Text)  # encrypted filename
    kind: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(30), default='queued')
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Draft(Base):
    __tablename__ = 'drafts'
    document_id: Mapped[str] = mapped_column(ForeignKey('documents.id'), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    selections: Mapped[list] = mapped_column(JSON, default=list)
    decisions: Mapped[dict] = mapped_column(JSON, default=dict)


class Export(Base):
    __tablename__ = 'exports'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey('documents.id'), index=True)
    version: Mapped[int] = mapped_column(Integer)
    selections: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default='queued')
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey('documents.id'), index=True)
    export_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state: Mapped[str] = mapped_column(String(30), default='queued')
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    timings: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RuleTemplate(Base):
    __tablename__ = 'rule_templates'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    rules: Mapped[str] = mapped_column(Text)  # encrypted JSON: regex can contain literals


class Audit(Base):
    __tablename__ = 'audit_events'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    actor: Mapped[str] = mapped_column(String(30), default='admin')
    action: Mapped[str] = mapped_column(String(40))
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoginSession(Base):
    __tablename__ = 'sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf: Mapped[str] = mapped_column(String(64))
    expires: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def audit(db, action, document_id=None, **details):
    db.add(Audit(action=action, document_id=document_id, details=details))
