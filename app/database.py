from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

SQLALCHEMY_DATABASE_URL = "sqlite:///./data/sentinel.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def init_db():
    """Crée les tables si elles n'existent pas."""
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    # Migration manuelle pour ajouter smtp_recipient
    from sqlalchemy import text
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE global_config ADD COLUMN smtp_recipient VARCHAR DEFAULT ''"))
            conn.commit()
        except Exception:
            pass  # La colonne existe déjà
