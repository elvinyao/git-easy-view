"""Administrative command entry point."""

import argparse
import getpass
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import make_engine
from app.models import Base, User


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="gev")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="Create tables in a development database")
    demo = commands.add_parser("demo", help="Seed an EMPTY development database with explicit example data")
    demo.add_argument("--password-env", default="GEV_DEMO_PASSWORD")
    demo.add_argument("--member-password-env", default="GEV_DEMO_MEMBER_PASSWORD")
    create_user = commands.add_parser("create-user", help="Create a local user")
    create_user.add_argument("--username", required=True)
    create_user.add_argument("--admin", action="store_true")
    create_user.add_argument("--password-env", default="GEV_ADMIN_PASSWORD")
    args = parser.parse_args(argv)
    settings = Settings()
    engine = make_engine(settings.database_url)
    try:
        if args.command == "init-db":
            if settings.production:
                parser.error("init-db is for development only; use Alembic migrations in production")
            Base.metadata.create_all(engine)
            print("Development database initialized")
            return 0

        if args.command == "demo":
            from app.demo import create_demo
            if settings.production:
                parser.error("demo is for development only")
            password = os.environ.get(args.password_env) or getpass.getpass("Demo administrator password: ")
            member_password = os.environ.get(args.member_password_env) or getpass.getpass("Demo member password: ")
            try:
                create_demo(settings, admin_password=password, member_password=member_password)
            except ValueError as error:
                parser.error(str(error))
            print("Example data created; users: demo-admin, demo-member")
            return 0

        username = args.username.strip()
        if not username:
            parser.error("username must not be empty")
        password = os.environ.get(args.password_env) or getpass.getpass("Password: ")
        if not password:
            parser.error("password must not be empty")
        with Session(engine) as session:
            if session.scalar(select(User.id).where(User.username == username)) is not None:
                parser.error("username already exists")
            user = User(username=username, is_admin=args.admin)
            user.set_password(password)
            session.add(user)
            session.commit()
        print(f"Created user {username}")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
