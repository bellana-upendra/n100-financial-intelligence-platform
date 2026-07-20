from pathlib import Path
import sqlite3

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "nifty100.db"
SECTOR_PATH = ROOT / "data" / "raw" / "sectors.xlsx"


def normalize_company_id(value):
    return str(value).strip().upper()


def existing_columns(connection, table):
    return {
        row[1] for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def main():
    sectors = pd.read_excel(SECTOR_PATH)

    required = {
        "company_id",
        "broad_sector",
        "sub_sector",
        "index_weight_pct",
        "market_cap_category",
    }

    missing = required.difference(sectors.columns)

    if missing:
        raise RuntimeError(f"Missing sector columns: {sorted(missing)}")

    sectors["normalized_id"] = sectors["company_id"].map(normalize_company_id)

    if sectors["normalized_id"].duplicated().any():
        duplicate_ids = sectors.loc[
            sectors["normalized_id"].duplicated(keep=False),
            "company_id",
        ].tolist()

        raise RuntimeError(f"Duplicate sector company IDs: {duplicate_ids}")

    with sqlite3.connect(DB_PATH) as connection:
        companies = pd.read_sql_query(
            "SELECT company_id FROM companies",
            connection,
        )

        company_lookup = {
            normalize_company_id(company_id): company_id
            for company_id in companies["company_id"]
        }

        unmatched = sorted(set(sectors["normalized_id"]) - set(company_lookup))

        if unmatched:
            raise RuntimeError(f"Sector company IDs not found: {unmatched}")

        columns = existing_columns(connection, "companies")

        additions = {
            "broad_sector": "TEXT",
            "sub_sector": "TEXT",
            "index_weight_pct": "REAL",
            "market_cap_category": "TEXT",
        }

        for column, data_type in additions.items():
            if column not in columns:
                connection.execute(
                    f"ALTER TABLE companies " f'ADD COLUMN "{column}" {data_type}'
                )

        updated = 0

        for row in sectors.itertuples(index=False):
            actual_company_id = company_lookup[row.normalized_id]

            cursor = connection.execute(
                """
                UPDATE companies
                SET broad_sector = ?,
                    sub_sector = ?,
                    index_weight_pct = ?,
                    market_cap_category = ?
                WHERE company_id = ?
                """,
                (
                    row.broad_sector,
                    row.sub_sector,
                    row.index_weight_pct,
                    row.market_cap_category,
                    actual_company_id,
                ),
            )

            updated += cursor.rowcount

        connection.commit()

        financial_count = connection.execute("""
            SELECT COUNT(*)
            FROM companies
            WHERE LOWER(TRIM(broad_sector)) = 'financials'
            """).fetchone()[0]

        missing_sector_count = connection.execute("""
            SELECT COUNT(*)
            FROM companies
            WHERE broad_sector IS NULL
               OR TRIM(broad_sector) = ''
            """).fetchone()[0]

    print(f"Companies updated: {updated}")
    print(f"Financials companies: {financial_count}")
    print(f"Companies without sector: {missing_sector_count}")

    if updated != 92:
        raise RuntimeError(f"Expected 92 updates but found {updated}")

    if financial_count != 19:
        print(
            "WARNING: Sprint requirement mentions 19 Financials "
            f"companies, but the current sectors.xlsx contains "
            f"{financial_count}. The source classifications will "
            "be used without modification."
        )

    if missing_sector_count != 0:
        raise RuntimeError("Some companies do not have sector mappings")

    print("Company-sector mapping completed successfully.")


if __name__ == "__main__":
    main()
