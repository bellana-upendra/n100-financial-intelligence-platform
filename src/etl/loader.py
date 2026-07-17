from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from loguru import logger

from src.etl.normaliser import normalise_dataframe, snake_case

LOAD_ORDER = [
    "sectors",
    "companies",
    "profitandloss",
    "balancesheet",
    "cashflow",
    "analysis",
    "documents",
    "prosandcons",
    "stock_prices",
    "peer_groups",
    "financial_ratios",
    "market_cap",
]

PRIMARY_KEYS = {
    "sectors": ["sector_id"],
    "companies": ["company_id"],
    "profitandloss": ["company_id", "year"],
    "balancesheet": ["company_id", "year"],
    "cashflow": ["company_id", "year"],
    "analysis": ["id"],
    "stock_prices": ["company_id", "date"],
    "peer_groups": ["id"],
    "financial_ratios": ["company_id", "year"],
    "market_cap": ["company_id", "year"],
}


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML configuration file."""

    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def read_source(
    path: Path,
    source_type: str,
    sheet: str | int | None = None,
    header: int = 0,
) -> pd.DataFrame:
    """Read an Excel or CSV source file."""

    source_type = source_type.strip().lower()

    if source_type == "excel":
        return pd.read_excel(
            path,
            sheet_name=sheet if sheet is not None else 0,
            header=header,
        )

    if source_type == "csv":
        return pd.read_csv(
            path,
            header=header,
        )

    raise ValueError(f"Unsupported source type: {source_type}")


def collect_frames(
    raw_dir: Path,
    config_path: Path,
) -> tuple[
    dict[str, pd.DataFrame],
    list[dict[str, Any]],
]:
    """Read configured sources and group them by target table."""

    config = load_yaml(config_path)

    frames_by_table: dict[
        str,
        list[pd.DataFrame],
    ] = {}

    audit: list[dict[str, Any]] = []

    for source in config.get("sources", []):
        filename = str(source.get("file", "")).strip()

        source_type = str(source.get("type", "")).strip().lower()

        mappings = source.get("mappings", []) or []

        if not filename or filename.startswith("REPLACE_ME"):
            audit.append(
                {
                    "source_file": (filename or "<blank>"),
                    "sheet": "",
                    "table": "",
                    "rows_read": 0,
                    "rows_loaded": 0,
                    "rows_rejected": 0,
                    "status": "SKIPPED",
                    "notes": ("Replace placeholder " "in table_config.yml"),
                }
            )
            continue

        path = raw_dir / filename

        if not path.exists():
            for mapping in mappings or [{}]:
                audit.append(
                    {
                        "source_file": filename,
                        "sheet": mapping.get(
                            "sheet",
                            "",
                        ),
                        "table": mapping.get(
                            "table",
                            "",
                        ),
                        "rows_read": 0,
                        "rows_loaded": 0,
                        "rows_rejected": 0,
                        "status": "ERROR",
                        "notes": (f"File not found: {path}"),
                    }
                )

            continue

        if not mappings:
            audit.append(
                {
                    "source_file": filename,
                    "sheet": "",
                    "table": "",
                    "rows_read": 0,
                    "rows_loaded": 0,
                    "rows_rejected": 0,
                    "status": "SKIPPED",
                    "notes": ("No table mappings configured"),
                }
            )
            continue

        for mapping in mappings:
            table = str(mapping.get("table", "")).strip()

            sheet = mapping.get("sheet")

            header = int(mapping.get("header", 0) or 0)

            if not table:
                audit.append(
                    {
                        "source_file": filename,
                        "sheet": sheet or "",
                        "table": "",
                        "rows_read": 0,
                        "rows_loaded": 0,
                        "rows_rejected": 0,
                        "status": "SKIPPED",
                        "notes": ("Target table is missing " "from mapping"),
                    }
                )
                continue

            try:
                df = read_source(
                    path=path,
                    source_type=source_type,
                    sheet=sheet,
                    header=header,
                )

                rows_read = len(df)

                # Convert every original column name
                # to snake_case before applying mappings.
                df.columns = [snake_case(column) for column in df.columns]

                # Convert the YAML source-column names
                # to snake_case so that they match the
                # normalised DataFrame columns.
                rename_mapping = {
                    snake_case(source_column): target_column
                    for (
                        source_column,
                        target_column,
                    ) in (mapping.get("rename") or {}).items()
                }

                df = df.rename(columns=rename_mapping)

                df = normalise_dataframe(df)

                # sectors.xlsx contains one sector value
                # per company. The SQLite sectors table is
                # a dimension table, so it needs only unique
                # sector names with newly generated IDs.
                if table == "sectors":
                    if "sector_name" not in df.columns:
                        raise ValueError(
                            "The sectors source must " "contain a sector_name column"
                        )

                    df = (
                        df[["sector_name"]]
                        .dropna(subset=["sector_name"])
                        .drop_duplicates(subset=["sector_name"])
                        .reset_index(drop=True)
                    )

                    df.insert(
                        0,
                        "sector_id",
                        range(
                            1,
                            len(df) + 1,
                        ),
                    )

                # Preserve source information for
                # audit and rejection reporting.
                df["__source_file"] = filename

                df["__source_sheet"] = str(sheet if sheet is not None else "")

                frames_by_table.setdefault(
                    table,
                    [],
                ).append(df)

                audit.append(
                    {
                        "source_file": filename,
                        "sheet": sheet or "",
                        "table": table,
                        "rows_read": rows_read,
                        "rows_loaded": 0,
                        "rows_rejected": 0,
                        "status": "READ",
                        "notes": "",
                    }
                )

                logger.info(
                    "Read {} rows from {} " "for table {}",
                    rows_read,
                    filename,
                    table,
                )

            except Exception as exc:
                logger.exception(
                    "Read failed for file={}, " "sheet={}, table={}",
                    filename,
                    sheet,
                    table,
                )

                audit.append(
                    {
                        "source_file": filename,
                        "sheet": sheet or "",
                        "table": table,
                        "rows_read": 0,
                        "rows_loaded": 0,
                        "rows_rejected": 0,
                        "status": "ERROR",
                        "notes": str(exc),
                    }
                )

    frames = {
        table: pd.concat(
            parts,
            ignore_index=True,
            sort=False,
        )
        for table, parts in frames_by_table.items()
        if parts
    }

    return frames, audit


def clean_frames(
    frames: dict[str, pd.DataFrame],
) -> tuple[
    dict[str, pd.DataFrame],
    list[dict[str, Any]],
]:
    """Reject invalid primary and company foreign keys."""

    rejections: list[dict[str, Any]] = []
    result: dict[str, pd.DataFrame] = {}

    # Validate primary keys.
    for table, df in frames.items():
        cleaned = df.copy()

        pk = [
            column
            for column in PRIMARY_KEYS.get(table, [])
            if column in cleaned.columns
        ]

        if pk:
            null_pk_mask = cleaned[pk].isna().any(axis=1)

            duplicate_pk_mask = cleaned.duplicated(
                pk,
                keep="first",
            )

            reject_mask = null_pk_mask | duplicate_pk_mask

            for idx in cleaned.index[reject_mask]:
                if null_pk_mask.loc[idx]:
                    reason = "Null primary key value: " f"{pk}"
                else:
                    reason = "Duplicate primary key " f"value: {pk}"

                rejections.append(
                    {
                        "table": table,
                        "row_index": int(idx),
                        "severity": "CRITICAL",
                        "reason": reason,
                        "source_file": (
                            cleaned.at[
                                idx,
                                "__source_file",
                            ]
                            if "__source_file" in cleaned.columns
                            else ""
                        ),
                    }
                )

            cleaned = cleaned.loc[~reject_mask].copy()

        result[table] = cleaned.reset_index(drop=True)

    # Validate every company_id foreign key
    # against companies.company_id.
    companies_df = result.get(
        "companies",
        pd.DataFrame(),
    )

    if "company_id" in companies_df.columns:
        company_ids = set(companies_df["company_id"].dropna())
    else:
        company_ids = set()

    for table, df in list(result.items()):
        if table == "companies" or "company_id" not in df.columns or not company_ids:
            continue

        invalid_mask = df["company_id"].notna() & ~df["company_id"].isin(company_ids)

        for idx in df.index[invalid_mask]:
            rejections.append(
                {
                    "table": table,
                    "row_index": int(idx),
                    "severity": "CRITICAL",
                    "reason": ("company_id not found " "in companies"),
                    "source_file": (
                        df.at[
                            idx,
                            "__source_file",
                        ]
                        if "__source_file" in df.columns
                        else ""
                    ),
                }
            )

        result[table] = df.loc[~invalid_mask].reset_index(drop=True)

    return result, rejections


def table_columns(
    conn: sqlite3.Connection,
    table: str,
) -> list[str]:
    """Return columns defined for a SQLite table."""

    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()

    return [row[1] for row in rows]


def load_to_database(
    frames: dict[str, pd.DataFrame],
    db_path: Path,
    schema_path: Path,
    audit: list[dict[str, Any]],
) -> None:
    """Create SQLite database and load clean frames."""

    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: " f"{schema_path}")

    db_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if db_path.exists():
        db_path.unlink()

    try:
        # closing() ensures that Windows releases
        # the database file before error cleanup.
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")

            schema_sql = schema_path.read_text(encoding="utf-8")

            conn.executescript(schema_sql)

            for table in LOAD_ORDER:
                df = frames.get(table)

                if df is None or df.empty:
                    logger.warning(
                        "No data available for {}",
                        table,
                    )
                    continue

                allowed_columns = table_columns(
                    conn,
                    table,
                )

                if not allowed_columns:
                    logger.warning(
                        "Table {} does not exist " "in schema",
                        table,
                    )
                    continue

                insert_columns = [
                    column for column in df.columns if column in allowed_columns
                ]

                if not insert_columns:
                    logger.warning(
                        "No matching database " "columns for {}",
                        table,
                    )
                    continue

                insert_df = df[insert_columns].copy()

                # Keep each SQLite statement below the SQL variable limit.
                max_sql_variables = 900
                chunksize = max(
                    1,
                    max_sql_variables // len(insert_columns),
                )

                insert_df.to_sql(
                    name=table,
                    con=conn,
                    if_exists="append",
                    index=False,
                    method="multi",
                    chunksize=chunksize,
                )

                rows_loaded = len(insert_df)

                logger.info(
                    "Loaded {} rows into {}",
                    rows_loaded,
                    table,
                )

                # Update every matching audit entry.
                matching_audit_rows = [
                    row
                    for row in audit
                    if (row.get("table") == table and row.get("status") == "READ")
                ]

                for row in matching_audit_rows:
                    source_file = row.get(
                        "source_file",
                        "",
                    )

                    source_sheet = str(row.get("sheet", ""))

                    source_mask = pd.Series(
                        True,
                        index=df.index,
                    )

                    if "__source_file" in df.columns:
                        source_mask &= df["__source_file"].astype(str) == str(
                            source_file
                        )

                    if "__source_sheet" in df.columns:
                        source_mask &= df["__source_sheet"].astype(str) == source_sheet

                    actual_loaded = int(source_mask.sum())

                    row["rows_loaded"] = actual_loaded

                    row["rows_rejected"] = max(
                        int(
                            row.get(
                                "rows_read",
                                0,
                            )
                        )
                        - actual_loaded,
                        0,
                    )

                    row["status"] = "LOADED"

            conn.commit()

    except Exception:
        logger.exception(
            "Database loading failed for {}",
            db_path,
        )

        if db_path.exists():
            db_path.unlink()

        raise

    logger.success(
        "Database created successfully: {}",
        db_path,
    )
