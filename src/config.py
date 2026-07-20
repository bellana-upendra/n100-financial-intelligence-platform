from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    project_root: Path
    raw_data_dir: Path
    processed_data_dir: Path
    output_dir: Path
    database_path: Path
    config_path: Path
    log_level: str


def get_settings() -> Settings:
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")

    def resolve(name: str, default: str) -> Path:
        value = Path(os.getenv(name, default))
        return value if value.is_absolute() else project_root / value

    return Settings(
        project_root=project_root,
        raw_data_dir=resolve("RAW_DATA_DIR", "data/raw"),
        processed_data_dir=resolve("PROCESSED_DATA_DIR", "data/processed"),
        output_dir=resolve("OUTPUT_DIR", "output"),
        database_path=resolve(
            "DATABASE_PATH",
            "data/processed/nifty100.db",
        ),
        config_path=resolve("CONFIG_PATH", "config/table_config.yml"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
