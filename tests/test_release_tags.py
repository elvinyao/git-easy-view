from sqlalchemy import select
from sqlalchemy.orm import Session

from app.git_service import GitService
from app.models import GitTag
from test_jobs import system
from test_sync import remote, git, run


def test_lightweight_annotated_and_deleted_tags_sync(system, remote):
    git(remote, 'tag', 'v1')
    git(remote, 'tag', '-a', 'v2', '-m', 'Annotated')
    run(system, remote)
    with Session(system.engine) as session:
        tags = {t.name: t for t in session.scalars(select(GitTag))}
        assert tags['v1'].commit_oid == tags['v2'].commit_oid == git(remote, 'rev-parse', 'main')
        assert tags['v2'].object_oid != tags['v2'].commit_oid
    git(remote, 'tag', '-f', 'v1', 'feature/one')
    git(remote, 'tag', '-d', 'v2')
    run(system, remote)
    with Session(system.engine) as session:
        tags = {t.name: t for t in session.scalars(select(GitTag))}
        assert tags['v1'].commit_oid == git(remote, 'rev-parse', 'feature/one')
        assert tags['v2'].deleted


def test_tag_count_limit_preserves_previous_snapshot(system, remote, monkeypatch):
    git(remote, 'tag', 'v1')
    run(system, remote)
    def fail(self):
        raise ValueError('tag snapshot failed')
    monkeypatch.setattr(GitService, 'tags', fail)
    git(remote, 'tag', '-d', 'v1')
    run(system, remote)
    with Session(system.engine) as session:
        assert not session.scalar(select(GitTag)).deleted


def test_sqlite_migration_upgrade_preserves_old_rows(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from app.db import make_engine
    from sqlalchemy import text, inspect
    database = 'sqlite:///' + str(tmp_path / 'migration.db')
    monkeypatch.setenv('GEV_DATABASE_URL', database)
    monkeypatch.setenv('GEV_PRODUCTION', 'false')
    config = Config('alembic.ini')
    command.upgrade(config, '0002')
    engine = make_engine(database)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO projects(name,slug,created_at) VALUES ('Existing','existing',CURRENT_TIMESTAMP)"))
    command.upgrade(config, 'head')
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT name FROM projects')) == 'Existing'
        assert conn.scalar(text('SELECT version_num FROM alembic_version')) == '0003'
        assert {'git_tags','release_sources','release_plans','release_items','deployments'} <= set(inspect(conn).get_table_names())
    command.downgrade(config, '0002')
    command.upgrade(config, 'head')
    engine.dispose()
