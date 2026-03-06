import logging
from datetime import datetime

import bcrypt
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def set_password(self, password: str) -> None:
        if not password:
            raise ValueError("Password cannot be empty")

        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12))
        self.password_hash = hashed.decode("utf-8")

    def check_password(self, password: str) -> bool:
        if not password or not self.password_hash:
            return False

        try:
            return bcrypt.checkpw(password.encode("utf-8"), self.password_hash.encode("utf-8"))
        except ValueError:
            return False


def initialize_database(default_username: str, default_password: str | None, logger=None) -> bool:
    """
    Initialize schema and default admin.

    Returns True when authentication is enabled, False when bootstrapped in open mode
    due to missing WEB_UI_PASSWORD on first run.
    """
    logger = logger or logging.getLogger(__name__)

    db.create_all()

    existing_user = User.query.first()
    if existing_user is not None:
        return True

    if not default_password:
        logger.warning(
            "Authentication is not configured: no users found and WEB_UI_PASSWORD is not set. "
            "Web UI will remain accessible without login until a user is created."
        )
        return False

    username = (default_username or "admin").strip() or "admin"
    admin_user = User(username=username)
    admin_user.set_password(default_password)

    db.session.add(admin_user)
    db.session.commit()

    logger.info("Created default web UI admin user '%s'", username)
    return True
