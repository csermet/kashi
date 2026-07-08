"""Engine/session factory. Synchronous by design (plan: no async DB layer)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kashi_server.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
