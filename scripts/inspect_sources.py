from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
OUTPUT_DIR = PROJECT_ROOT / "output"

SOURCE_SETTINGS = {
    "analysis.xlsx": {
        "sheet": "Analysis",
        "header": 1,
    },
    "balancesheet.xlsx": {
        "sheet": "Balance Sheet",
        "header": 1,
    },
    "cashflow.xlsx": {
        "sheet": "Cash Flow",
        "header": 1,
    },
    "companies.xlsx": {
        "sheet": "Companies",
        "header": 1,
    },
    "documents.xlsx": {
        "sheet": "Documents",
        "header": 1,
    },
    "profitandloss.xlsx": {
        "sheet": "Profit & Loss",
        "header": 1,
    },
    "prosandcons.xlsx": {
        "sheet": "Pros & Cons",
        "header": 1,
    },
    "financial_ratios.xlsx": {
        "sheet": "Sheet1",
        "header": 0,
    },
    "market_cap.xlsx": {
        "sheet": "Sheet1",
        "header": 0,
    },
    "peer_groups.xlsx": {
        "sheet": "Sheet1",
        "header": 0,
    },
    "sectors.xlsx": {
        "sheet": "Sheet1",
        "header": 0,
    },
    "stock_prices.xlsx": {
        "sheet": "Sheet1",
        "header": 0,
    },
}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    output_lines: list[str] = []

    for filename, settings in SOURCE_SETTINGS.items():
        file_path = RAW_DIR / filename

        if not file_path.exists():
            output_lines.append(
                f"FILE: {filename}\nSTATUS: MISSING\n"
            )
            continue

        sheet_name = settings["sheet"]
        header_row = settings["header"]

        try:
            dataframe = pd.read_excel(
                file_path,
                sheet_name=sheet_name,
                header=header_row,
            )

            dataframe = dataframe.dropna(how="all")

            output_lines.extend(
                [
                    f"FILE: {filename}",
                    f"SHEET: {sheet_name}",
                    f"HEADER ROW: {header_row}",
                    f"ROWS: {len(dataframe)}",
                    f"COLUMNS: {dataframe.columns.tolist()}",
                    "FIRST TWO RECORDS:",
                    dataframe.head(2).to_string(index=False),
                    "",
                    "-" * 100,
                    "",
                ]
            )

        except Exception as error:
            output_lines.extend(
                [
                    f"FILE: {filename}",
                    f"STATUS: ERROR",
                    f"ERROR: {error}",
                    "",
                    "-" * 100,
                    "",
                ]
            )

    output_path = OUTPUT_DIR / "source_inventory_corrected.txt"

    output_path.write_text(
        "\n".join(output_lines),
        encoding="utf-8",
    )

    print(f"Created: {output_path}")


if __name__ == "__main__":
    main()