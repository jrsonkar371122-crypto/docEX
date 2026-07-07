"""Create (or promote) the first admin user.

Usage (inside the backend container):
    python -m scripts.create_admin --email admin@example.com \
        --password 'S3cure!pass' --name "Site Admin"

If the user already exists, their password is reset and they are promoted to
admin and re-activated. Runs synchronously so it works during first-run setup.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import Role, User
from app.services.auth_service import hash_password


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the first admin user.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()

    if len(args.password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        return 2

    engine = create_engine(settings.sync_database_url, pool_pre_ping=True)
    with Session(engine) as db:
        user = db.execute(
            select(User).where(User.email == args.email)
        ).scalar_one_or_none()
        if user is None:
            user = User(
                email=args.email,
                hashed_password=hash_password(args.password),
                full_name=args.name,
                role=Role.admin,
                is_active=True,
            )
            db.add(user)
            action = "created"
        else:
            user.hashed_password = hash_password(args.password)
            user.role = Role.admin
            user.is_active = True
            user.full_name = args.name
            action = "updated"
        db.commit()

    print(f"Admin user {action}: {args.email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
