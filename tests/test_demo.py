import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import make_engine
from app.models import Base, Branch, Comparison, PlatformConnection, User


def test_demo_is_explicit_and_real_git(tmp_path):
    from app.demo import create_demo

    settings = Settings(production=False, database_url=f"sqlite:///{tmp_path}/demo.db", cache_root=str(tmp_path / "git"))
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    create_demo(settings, admin_password="chosen password", member_password="member password", repositories=2, branches=55)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Branch)) == 110
        assert session.scalar(select(User).where(User.username == "demo-admin")).check_password("chosen password")
        assert all(c.auth_disabled and "示例" in c.last_error for c in session.scalars(select(PlatformConnection)))
        assert set(session.scalars(select(Comparison.state))) >= {"identical", "contained", "diverged"}
    with pytest.raises(ValueError, match="empty"):
        create_demo(settings, admin_password="x", member_password="y")
    engine.dispose()


def test_demo_refuses_production(tmp_path):
    from app.demo import create_demo
    settings = Settings(production=False)
    settings.production = True
    with pytest.raises(ValueError, match="development"):
        create_demo(settings, admin_password="x", member_password="y")


def test_demo_is_never_eligible_for_external_dispatch(tmp_path, monkeypatch):
    import app.demo as demo
    from app.models import SyncJob
    from app.jobs import ACTIVE
    settings = Settings(production=False, database_url=f"sqlite:///{tmp_path}/demo.db", cache_root=str(tmp_path / "git"))
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    generate = demo.generate_history
    def interrupted_seed(path, branches):
        with Session(engine) as session:
            assert all(c.auth_disabled for c in session.scalars(select(PlatformConnection)))
            assert not list(session.scalars(select(SyncJob).where(SyncJob.status.in_(ACTIVE))))
        return generate(path, branches)
    monkeypatch.setattr(demo, "generate_history", interrupted_seed)
    demo.create_demo(settings, admin_password="x", member_password="y", repositories=1)
    engine.dispose()
