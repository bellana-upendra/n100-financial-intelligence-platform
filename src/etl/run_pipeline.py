from __future__ import annotations

import sys

import pandas as pd
from loguru import logger

from src.config import get_settings
from src.etl.loader import clean_frames, collect_frames, load_to_database
from src.etl.validator import validate_frames


def main():
    settings = get_settings()
    settings.raw_data_dir.mkdir(parents=True, exist_ok=True)
    settings.processed_data_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    (settings.project_root / "logs").mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(sys.stderr, level=settings.log_level)
    logger.add(settings.project_root / "logs" / "etl.log", rotation="1 MB")

    frames, audit = collect_frames(settings.raw_data_dir, settings.config_path)
    if not frames:
        pd.DataFrame(audit).to_csv(settings.output_dir / "load_audit.csv", index=False)
        logger.error("No data loaded. Add files and update config/table_config.yml.")
        return 2

    frames, rejections = clean_frames(frames)
    load_to_database(
        frames,
        settings.database_path,
        settings.project_root / "db" / "schema.sql",
        audit,
    )

    failures = validate_frames(frames, settings.database_path)
    failures.to_csv(settings.output_dir / "validation_failures.csv", index=False)
    pd.DataFrame(audit).to_csv(settings.output_dir / "load_audit.csv", index=False)
    pd.DataFrame(rejections).to_csv(settings.output_dir / "rejected_rows.csv", index=False)

    critical = int((failures["severity"] == "CRITICAL").sum()) if not failures.empty else 0
    logger.info("Finished with {} critical failures", critical)
    return 1 if critical else 0


if __name__ == "__main__":
    raise SystemExit(main())
