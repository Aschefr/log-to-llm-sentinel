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

    # Migrations manuelles (sûres : ignorées si la colonne existe déjà)
    from sqlalchemy import text
    migrations = [
        "ALTER TABLE global_config ADD COLUMN smtp_recipient VARCHAR DEFAULT ''",
        "ALTER TABLE global_config ADD COLUMN smtp_ssl_mode VARCHAR DEFAULT 'starttls'",
        "ALTER TABLE global_config ADD COLUMN debug_mode BOOLEAN DEFAULT 0",
        "ALTER TABLE rules ADD COLUMN context_lines INTEGER DEFAULT 5",
        "ALTER TABLE global_config ADD COLUMN apprise_tags VARCHAR DEFAULT ''",
        "ALTER TABLE global_config ADD COLUMN apprise_max_chars INTEGER DEFAULT 1900",
    ]
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                pass  # Colonne déjà présente
