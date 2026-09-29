"""Explicit development fixtures: real Git objects, clearly fictional platform data."""
import os
from pathlib import Path
import subprocess
import tempfile

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import make_engine
from .models import Base, PlatformConnection, Project, ProjectMember, ProjectRepository, Repository, SyncJob, User
from .providers import Repo, PR
from .models import utc_now
from .jobs import repository_lock
from .sync import _analyze, _api_snapshot, _publish_git, _refresh_cache


def generate_history(path, branches=55):
    """A small graph with contained, identical and genuinely divergent tips."""
    path = Path(path)
    env = dict(os.environ, GIT_AUTHOR_NAME="Example", GIT_AUTHOR_EMAIL="example@example.invalid",
               GIT_COMMITTER_NAME="Example", GIT_COMMITTER_EMAIL="example@example.invalid")
    def git(*args, input=None):
        return subprocess.check_output(["git", "-C", str(path), *args], input=input, env=env, stderr=subprocess.DEVNULL).decode().strip()
    path.mkdir(parents=True)
    git("init", "--bare")
    tree = git("mktree", input=b"")
    root = git("commit-tree", tree, "-m", "Example root")
    main = git("commit-tree", tree, "-p", root, "-m", "Example main advance")
    feature = git("commit-tree", tree, "-p", root, "-m", "Example independent feature")
    tips = {"main": main, "release/2026.09": root, "feature/login": feature}
    tips.update({f"feature/sample-{i:03}": (root, main, feature)[i % 3] for i in range(branches - 3)})
    git("update-ref", "--stdin", input="".join(f"update refs/heads/{name} {oid}\n" for name, oid in tips.items()).encode())
    git("symbolic-ref", "HEAD", "refs/heads/main")
    return tips


class DemoProvider:
    web_root = "https://example.invalid"
    def __init__(self, connection=None, token=None): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def repository(self, external_id):
        name = f"example/repository-{int(external_id):03}"
        return Repo(external_id, name, f"{self.web_root}/{name}", f"{self.web_root}/{name}.git", "main")
    def repositories(self): return [self.repository(str(i)) for i in range(1, 57)]
    def check(self): return {"identity": "EXAMPLE ONLY", "version": None, "capabilities": {}}
    def pull_requests(self, external_id, since, **kwargs):
        now = utc_now()
        return [PR(f"{external_id}-example-{state}", f"示例 PR · {state} · 非真实平台记录",
                   f"{self.web_root}/example/pull/{external_id}", state, external_id, "feature/login", None,
                   external_id, "main", None, now, now if state == "merged" else None)
                for state in ("open", "merged")]


def create_demo(settings, *, admin_password, member_password, repositories=3, branches=55):
    if settings.production:
        raise ValueError("Demo requires explicit development mode")
    if not admin_password or not member_password or not 1 <= repositories <= 100 or not 3 <= branches <= 100:
        raise ValueError("Provide passwords and bounded fixture sizes")
    engine = make_engine(settings.database_url)
    try:
        with Session(engine) as session:
            # Refuse all existing application data, including partially initialized installations.
            if any(session.execute(select(table).limit(1)).first() for table in Base.metadata.sorted_tables):
                raise ValueError("Demo requires an empty database")
            admin = User(username="demo-admin", is_admin=True)
            admin.set_password(admin_password)
            member = User(username="demo-member")
            member.set_password(member_password)
            project = Project(name="示例 · 产品研发", slug="example-product", description="全部为生成的示例数据；Git 历史真实，平台记录为虚构。")
            private = Project(name="示例 · 管理员专属项目", slug="example-private")
            connection = PlatformConnection(name="示例平台 · 无外部连接", kind="github", base_url=DemoProvider.web_root,
                                            auth_disabled=True, last_error="示例数据：虚构平台已暂停；本地 Git 比较可用。")
            session.add_all([admin, member, project, private, connection])
            session.flush()
            session.add(ProjectMember(project_id=project.id, user_id=member.id))
            ids = []
            for i in range(1, repositories + 1):
                meta = DemoProvider().repository(str(i))
                repo = Repository(connection_id=connection.id, external_id=str(i), name=meta.full_name.split("/")[-1],
                                  full_name=meta.full_name, web_url=meta.web_url, clone_url=meta.clone_url,
                                  default_branch="main", requested_generation=1)
                session.add(repo)
                session.flush()
                session.add(ProjectRepository(project_id=project.id, repository_id=repo.id))
                # A terminal, explicitly incomplete record is never dispatched,
                # including if this development command is SIGKILLed mid-seed.
                job = SyncJob(repository_id=repo.id, generation=1, status="failed", error="Local example initialization incomplete")
                session.add(job)
                session.flush()
                ids.append((repo.id, job.id))
            session.commit()
            cid = connection.id
        with tempfile.TemporaryDirectory(prefix="gev-example-") as temporary:
            source = Path(temporary) / "source.git"
            generate_history(source, branches)
            for rid, jid in ids:
                with repository_lock(settings, rid):
                    with Session(engine, expire_on_commit=False) as session:
                        repo = session.get(Repository, rid)
                        job = session.get(SyncJob, jid)
                        connection = session.get(PlatformConnection, cid)
                    # Reuse real publication/analysis while all provider access
                    # remains an explicit local fixture and the connection stays paused.
                    _api_snapshot(engine, job, connection, None, DemoProvider)
                    git = _refresh_cache(settings, repo, connection, None, local_source=source)
                    _publish_git(engine, job, connection, *_analyze(engine, job, git))
                    with Session(engine) as session:
                        row = session.get(SyncJob, jid)
                        row.status, row.error, row.finished_at = "succeeded", None, utc_now()
                        session.commit()
        return ids
    finally:
        engine.dispose()
