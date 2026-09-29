from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Round-trip aware UTC values on both PostgreSQL and SQLite."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


_password_hasher = PasswordHasher()


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    memberships: Mapped[list["ProjectMember"]] = relationship(back_populates="user", cascade="all, delete-orphan")

    def set_password(self, password: str) -> None:
        self.password_hash = _password_hasher.hash(password)

    def check_password(self, password: str) -> bool:
        try:
            return _password_hasher.verify(self.password_hash, password)
        except (VerificationError, ValueError):
            return False


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    members: Mapped[list["ProjectMember"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    repositories: Mapped[list["ProjectRepository"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project: Mapped[Project] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class PlatformConnection(Base):
    __tablename__ = "platform_connections"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str] = mapped_column(String(2048))
    encrypted_token: Mapped[str | None] = mapped_column(Text)
    ca_bundle_path: Mapped[str | None] = mapped_column(String(2048))
    auth_disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    repositories: Mapped[list["Repository"]] = relationship(back_populates="connection")


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("connection_id", "external_id"), Index("ix_repositories_next_sync_at", "next_sync_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("platform_connections.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(1024))
    web_url: Mapped[str] = mapped_column(String(2048))
    clone_url: Mapped[str] = mapped_column(String(2048))
    default_branch: Mapped[str | None] = mapped_column(String(1024))
    sync_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    next_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    git_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    api_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    api_coverage_since: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sync_error: Mapped[str | None] = mapped_column(Text)
    api_error: Mapped[str | None] = mapped_column(Text)
    snapshot_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    api_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requested_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    connection: Mapped[PlatformConnection] = relationship(back_populates="repositories")
    projects: Mapped[list["ProjectRepository"]] = relationship(back_populates="repository", cascade="all, delete-orphan")
    branches: Mapped[list["Branch"]] = relationship(back_populates="repository", cascade="all, delete-orphan")


class ProjectRepository(Base):
    __tablename__ = "project_repositories"
    __table_args__ = (UniqueConstraint("project_id", "repository_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True)
    project: Mapped[Project] = relationship(back_populates="repositories")
    repository: Mapped[Repository] = relationship(back_populates="projects")


class Branch(Base):
    __tablename__ = "branches"
    __table_args__ = (UniqueConstraint("repository_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True)
    name: Mapped[str] = mapped_column(String(1024))
    head_oid: Mapped[str | None] = mapped_column(String(128))
    commit_time: Mapped[datetime | None] = mapped_column(UTCDateTime())
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    snapshot_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    repository: Mapped[Repository] = relationship(back_populates="branches")
    config: Mapped["BranchConfig | None"] = relationship(back_populates="branch", foreign_keys="BranchConfig.branch_id", uselist=False, cascade="all, delete-orphan")


class BranchConfig(Base):
    __tablename__ = "branch_configs"
    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"), unique=True)
    role: Mapped[str | None] = mapped_column(String(32))
    manual_source_branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id"))
    manual_targets: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    main_comparison_branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id"))
    active_release: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    branch: Mapped[Branch] = relationship(back_populates="config", foreign_keys=[branch_id])


class Comparison(Base):
    __tablename__ = "comparisons"
    __table_args__ = (UniqueConstraint("repository_id", "source_oid", "target_oid", "rule_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True)
    source_branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id"))
    target_branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id"))
    source_oid: Mapped[str] = mapped_column(String(128))
    target_oid: Mapped[str] = mapped_column(String(128))
    ahead: Mapped[int | None] = mapped_column(Integer)
    behind: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(32))
    merge_bases: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class PullRequest(Base):
    __tablename__ = "pull_requests"
    __table_args__ = (UniqueConstraint("connection_id", "external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("platform_connections.id"), index=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(1024))
    web_url: Mapped[str] = mapped_column(String(2048))
    state: Mapped[str] = mapped_column(String(32))
    source_repository_id: Mapped[int | None] = mapped_column(ForeignKey("repositories.id"))
    source_external_repo_id: Mapped[str | None] = mapped_column(String(255))
    source_branch: Mapped[str | None] = mapped_column(String(1024))
    source_oid: Mapped[str | None] = mapped_column(String(128))
    target_repository_id: Mapped[int | None] = mapped_column(ForeignKey("repositories.id"))
    target_external_repo_id: Mapped[str | None] = mapped_column(String(255))
    target_branch: Mapped[str | None] = mapped_column(String(1024))
    merge_oid: Mapped[str | None] = mapped_column(String(128))
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    merged_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    api_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class SyncJob(Base):
    __tablename__ = "sync_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True)
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    history_since: Mapped[datetime | None] = mapped_column(UTCDateTime())
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    enqueued_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    enqueue_error: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(128))
    entity_type: Mapped[str] = mapped_column(String(128))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
