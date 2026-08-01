"""Sprint 5 master build script.

Run from the repository root:

    python -m py_compile build_sprint5_all.py
    python build_sprint5_all.py

Execution order:
    1. NLP parser
    2. Pros and cons generator
    3. Cash-flow intelligence
    4. Capital-allocation summary
    5. Company tearsheets
    6. Sector reports
    7. Portfolio summary
    8. Final validations

The build is fail-fast: a non-zero subprocess exit code or any failed
validation stops the script immediately and returns exit code 1.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from src.config import get_settings


# =============================================================================
# CONFIGURATION
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent
SETTINGS = get_settings()


def resolve_project_path(value: object) -> Path:
    """Resolve a configured path relative to the repository root."""

    path = Path(str(value))
    return path if path.is_absolute() else PROJECT_ROOT / path


DATABASE_PATH = resolve_project_path(SETTINGS.database_path)
OUTPUT_DIR = resolve_project_path(SETTINGS.output_dir)

REPORTS_DIR = PROJECT_ROOT / "reports"
TEARSHEET_DIR = REPORTS_DIR / "tearsheets"
SECTOR_DIR = REPORTS_DIR / "sector"
PORTFOLIO_PATH = REPORTS_DIR / "portfolio" / "portfolio_summary.pdf"

EXPECTED_COMPANY_COUNT = 92
EXPECTED_SECTOR_COUNT = 11
MINIMUM_TEARSHEET_BYTES = 30 * 1024

ANALYSIS_PARSED_PATH = OUTPUT_DIR / "analysis_parsed.csv"
PARSE_FAILURES_PATH = OUTPUT_DIR / "parse_failures.csv"
PROS_CONS_PATH = OUTPUT_DIR / "pros_cons_generated.csv"
CASHFLOW_INTELLIGENCE_PATH = OUTPUT_DIR / "cashflow_intelligence.xlsx"
DISTRESS_ALERTS_PATH = OUTPUT_DIR / "distress_alerts.csv"
CAPITAL_DISTRIBUTION_PATH = OUTPUT_DIR / "capital_allocation_distribution.csv"
PATTERN_CHANGES_PATH = OUTPUT_DIR / "pattern_changes.csv"
SKIPPED_TEARSHEETS_PATH = OUTPUT_DIR / "skipped_tearsheets.csv"

REQUIRED_MODULE_FILES = (
    PROJECT_ROOT / "src" / "nlp" / "parser.py",
    PROJECT_ROOT / "src" / "nlp" / "pros_cons_generator.py",
    PROJECT_ROOT / "src" / "analytics" / "cashflow_kpis.py",
    PROJECT_ROOT / "src" / "reports" / "capital_allocation_report.py",
    PROJECT_ROOT / "src" / "reports" / "tearsheet.py",
    PROJECT_ROOT / "src" / "reports" / "sector_report.py",
    PROJECT_ROOT / "src" / "reports" / "portfolio_summary.py",
)


# =============================================================================
# ERROR AND STEP MODELS
# =============================================================================


class BuildFailure(RuntimeError):
    """Raised when a critical build step or validation fails."""


@dataclass(frozen=True)
class BuildStep:
    number: int
    name: str
    command: tuple[str, ...]


# =============================================================================
# DISPLAY HELPERS
# =============================================================================


def print_rule(character: str = "=", width: int = 76) -> None:
    """Print a consistent console rule."""

    print(character * width, flush=True)


def print_heading(text: str) -> None:
    """Print a major section heading."""

    print()
    print_rule()
    print(text)
    print_rule()


def format_command(command: Iterable[str]) -> str:
    """Format an argv sequence using Windows command-line quoting."""

    return subprocess.list2cmdline(list(command))


# =============================================================================
# PREFLIGHT
# =============================================================================


def require_file(path: Path, description: str) -> None:
    """Require a file to exist and be non-empty."""

    if not path.exists():
        raise BuildFailure(
            f"{description} was not found:\n  {path}"
        )

    if not path.is_file():
        raise BuildFailure(
            f"{description} is not a regular file:\n  {path}"
        )

    if path.stat().st_size <= 0:
        raise BuildFailure(
            f"{description} is empty:\n  {path}"
        )


def check_dependencies() -> None:
    """Verify dependencies used by generation and validation."""

    required_imports = {
        "matplotlib": "matplotlib",
        "numpy": "numpy",
        "openpyxl": "openpyxl",
        "pandas": "pandas",
        "pypdf": "pypdf",
        "reportlab": "reportlab",
    }

    missing: list[str] = []

    for package_name, module_name in required_imports.items():
        try:
            __import__(module_name)
        except ImportError:
            missing.append(package_name)

    if missing:
        raise BuildFailure(
            "Missing required Python package(s): "
            + ", ".join(sorted(missing))
            + "\nInstall them in the active virtual environment before rerunning."
        )


def database_company_count() -> int:
    """Return the number of distinct companies in the configured database."""

    require_file(
        DATABASE_PATH,
        "Configured SQLite database",
    )

    with sqlite3.connect(DATABASE_PATH) as connection:
        table_row = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'companies'
            LIMIT 1
            """
        ).fetchone()

        if table_row is None:
            raise BuildFailure(
                "The configured database has no companies table."
            )

        columns = {
            str(row[1])
            for row in connection.execute(
                'PRAGMA table_info("companies")'
            ).fetchall()
        }

        company_column = next(
            (
                candidate
                for candidate in (
                    "company_id",
                    "ticker",
                    "symbol",
                )
                if candidate in columns
            ),
            None,
        )

        if company_column is None:
            raise BuildFailure(
                "The companies table has no company_id, ticker, or symbol column."
            )

        row = connection.execute(
            f"""
            SELECT COUNT(DISTINCT "{company_column}")
            FROM "companies"
            WHERE "{company_column}" IS NOT NULL
              AND TRIM(CAST("{company_column}" AS TEXT)) <> ''
            """
        ).fetchone()

    count = int(row[0]) if row is not None else 0

    if count <= 0:
        raise BuildFailure(
            "The companies table contains no valid company identifiers."
        )

    return count


def run_preflight() -> int:
    """Run all preflight checks and return the database company count."""

    print_heading("Sprint 5 master build — preflight")

    if Path.cwd().resolve() != PROJECT_ROOT.resolve():
        print(
            "Note: the script was launched outside the repository root.\n"
            f"All subprocesses will run from:\n  {PROJECT_ROOT}",
            flush=True,
        )

    check_dependencies()

    for module_path in REQUIRED_MODULE_FILES:
        require_file(
            module_path,
            f"Required source module {module_path.name}",
        )

    company_count = database_company_count()

    if company_count != EXPECTED_COMPANY_COUNT:
        raise BuildFailure(
            f"Expected {EXPECTED_COMPANY_COUNT} companies, "
            f"but the database contains {company_count}."
        )

    print(f"Repository root:          {PROJECT_ROOT}")
    print(f"Database:                 {DATABASE_PATH}")
    print(f"Companies:                {company_count}")
    print("Dependencies:             PASS")
    print("Required source modules:  PASS")
    print("Preflight:                PASS")

    return company_count


# =============================================================================
# BUILD EXECUTION
# =============================================================================


def run_build_step(step: BuildStep) -> None:
    """Run one critical subprocess and stop on any non-zero exit code."""

    print_heading(
        f"Step {step.number}/8 — {step.name}"
    )
    print(
        "Command:",
        format_command(step.command),
        flush=True,
    )

    started = time.perf_counter()

    try:
        completed = subprocess.run(
            list(step.command),
            cwd=PROJECT_ROOT,
            check=False,
            env=None,
        )
    except OSError as exc:
        raise BuildFailure(
            f"Unable to start step {step.number} ({step.name}): {exc}"
        ) from exc

    elapsed = time.perf_counter() - started

    if completed.returncode != 0:
        raise BuildFailure(
            f"Step {step.number} failed: {step.name}\n"
            f"Command: {format_command(step.command)}\n"
            f"Exit code: {completed.returncode}\n"
            "The build stopped immediately; later steps were not run."
        )

    print()
    print(
        f"Step {step.number} completed successfully "
        f"in {elapsed:,.1f} seconds.",
        flush=True,
    )


def build_steps() -> tuple[BuildStep, ...]:
    """Return the ordered Sprint 5 build commands."""

    python = sys.executable

    return (
        BuildStep(
            1,
            "NLP parser",
            (
                python,
                "-m",
                "src.nlp.parser",
            ),
        ),
        BuildStep(
            2,
            "Pros and cons generator",
            (
                python,
                "-m",
                "src.nlp.pros_cons_generator",
            ),
        ),
        BuildStep(
            3,
            "Cash-flow intelligence",
            (
                python,
                "-m",
                "src.analytics.cashflow_kpis",
            ),
        ),
        BuildStep(
            4,
            "Capital-allocation summary",
            (
                python,
                "-m",
                "src.reports.capital_allocation_report",
            ),
        ),
        BuildStep(
            5,
            "Company tearsheets",
            (
                python,
                "-m",
                "src.reports.tearsheet",
                "--all",
            ),
        ),
        BuildStep(
            6,
            "Sector reports",
            (
                python,
                "-m",
                "src.reports.sector_report",
                "--all",
            ),
        ),
        BuildStep(
            7,
            "Portfolio summary",
            (
                python,
                "-m",
                "src.reports.portfolio_summary",
            ),
        ),
    )


# =============================================================================
# VALIDATION HELPERS
# =============================================================================


def read_csv_checked(
    path: Path,
    description: str,
    allow_empty: bool = False,
) -> pd.DataFrame:
    """Read a CSV after checking that it exists."""

    require_file(
        path,
        description,
    )

    try:
        frame = pd.read_csv(path)
    except Exception as exc:
        raise BuildFailure(
            f"Could not read {description}:\n  {path}\n{exc}"
        ) from exc

    if frame.empty and not allow_empty:
        raise BuildFailure(
            f"{description} contains no data rows:\n  {path}"
        )

    return frame


def read_excel_checked(
    path: Path,
    description: str,
) -> pd.DataFrame:
    """Read an Excel workbook after checking that it exists."""

    require_file(
        path,
        description,
    )

    try:
        frame = pd.read_excel(path)
    except Exception as exc:
        raise BuildFailure(
            f"Could not read {description}:\n  {path}\n{exc}"
        ) from exc

    if frame.empty:
        raise BuildFailure(
            f"{description} contains no data rows:\n  {path}"
        )

    return frame


def normalise_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with lowercase snake_case column names."""

    result = frame.copy()
    result.columns = [
        "_".join(
            part
            for part in "".join(
                character.lower()
                if character.isalnum()
                else " "
                for character in str(column)
            ).split()
            if part
        )
        for column in result.columns
    ]
    return result


def first_column(
    frame: pd.DataFrame,
    candidates: Iterable[str],
) -> str | None:
    """Return the first candidate column found."""

    available = set(frame.columns)

    return next(
        (
            candidate
            for candidate in candidates
            if candidate in available
        ),
        None,
    )


def require_columns(
    frame: pd.DataFrame,
    required: Iterable[str],
    description: str,
) -> None:
    """Require all named columns to exist."""

    required_set = set(required)
    missing = sorted(
        required_set - set(frame.columns)
    )

    if missing:
        raise BuildFailure(
            f"{description} is missing required column(s): "
            + ", ".join(missing)
        )


def require_unique_companies(
    frame: pd.DataFrame,
    company_count: int,
    description: str,
) -> None:
    """Require one non-duplicate row per company."""

    require_columns(
        frame,
        ("company_id",),
        description,
    )

    company_ids = (
        frame["company_id"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    if company_ids.isna().any() or (company_ids == "").any():
        raise BuildFailure(
            f"{description} contains blank company_id values."
        )

    if company_ids.duplicated().any():
        duplicates = sorted(
            company_ids[
                company_ids.duplicated(
                    keep=False
                )
            ].unique()
        )

        raise BuildFailure(
            f"{description} contains duplicate companies: "
            + ", ".join(duplicates[:15])
        )

    unique_count = int(
        company_ids.nunique()
    )

    if unique_count != company_count:
        raise BuildFailure(
            f"{description} contains {unique_count} companies; "
            f"expected {company_count}."
        )


def pdf_reader_class():
    """Return pypdf.PdfReader after the dependency preflight."""

    from pypdf import PdfReader

    return PdfReader


# =============================================================================
# FINAL VALIDATIONS
# =============================================================================


def validate_nlp_outputs() -> dict[str, int]:
    """Validate Day 29 parser outputs."""

    parsed = read_csv_checked(
        ANALYSIS_PARSED_PATH,
        "Parsed analysis CSV",
    )

    failures = read_csv_checked(
        PARSE_FAILURES_PATH,
        "Parse failures CSV",
        allow_empty=True,
    )

    total = len(parsed) + len(failures)

    if total <= 0:
        raise BuildFailure(
            "NLP parser outputs contain no source records."
        )

    return {
        "parsed_rows": len(parsed),
        "failure_rows": len(failures),
        "source_rows": total,
    }


def validate_pros_cons_outputs(
    company_count: int,
) -> dict[str, int]:
    """Validate Day 30 generated pros and cons coverage."""

    frame = normalise_columns(
        read_csv_checked(
            PROS_CONS_PATH,
            "Generated pros and cons CSV",
        )
    )

    company_column = first_column(
        frame,
        (
            "company_id",
            "ticker",
        ),
    )
    type_column = first_column(
        frame,
        (
            "type",
            "item_type",
            "pro_con_type",
            "category",
        ),
    )
    text_column = first_column(
        frame,
        (
            "text",
            "statement",
            "description",
            "content",
        ),
    )

    missing_labels: list[str] = []

    if company_column is None:
        missing_labels.append("company identifier")
    if type_column is None:
        missing_labels.append("pro/con type")
    if text_column is None:
        missing_labels.append("text")

    if missing_labels:
        raise BuildFailure(
            "Generated pros and cons CSV is missing: "
            + ", ".join(missing_labels)
        )

    frame["company_id"] = (
        frame[company_column]
        .astype("string")
        .str.strip()
        .str.upper()
    )
    frame["item_type"] = (
        frame[type_column]
        .astype("string")
        .str.strip()
        .str.lower()
    )
    frame["item_text"] = (
        frame[text_column]
        .astype("string")
        .str.strip()
    )

    if frame["item_text"].isna().any() or (
        frame["item_text"] == ""
    ).any():
        raise BuildFailure(
            "Generated pros and cons CSV contains blank text."
        )

    valid_types = frame[
        frame["item_type"].isin(
            ("pro", "con")
        )
    ].copy()

    coverage = (
        valid_types.groupby(
            "company_id"
        )["item_type"]
        .agg(set)
    )

    missing_coverage = [
        company_id
        for company_id, labels in coverage.items()
        if not {"pro", "con"}.issubset(labels)
    ]

    company_coverage = int(
        coverage.index.nunique()
    )

    if company_coverage != company_count:
        raise BuildFailure(
            f"Pros/cons coverage contains {company_coverage} companies; "
            f"expected {company_count}."
        )

    if missing_coverage:
        raise BuildFailure(
            "Companies missing a pro or con: "
            + ", ".join(
                sorted(missing_coverage)[:20]
            )
        )

    return {
        "rows": len(frame),
        "companies": company_coverage,
    }


def validate_cashflow_outputs(
    company_count: int,
) -> dict[str, int]:
    """Validate Day 31 cash-flow intelligence."""

    frame = normalise_columns(
        read_excel_checked(
            CASHFLOW_INTELLIGENCE_PATH,
            "Cash-flow intelligence workbook",
        )
    )

    required = (
        "company_id",
        "sector",
        "cfo_quality_score",
        "cfo_quality_label",
        "capex_intensity_pct",
        "capex_label",
        "fcf_cagr_5yr",
        "fcf_conversion_pct",
        "distress_flag",
        "deleveraging_flag",
        "capital_allocation_label",
    )

    require_columns(
        frame,
        required,
        "Cash-flow intelligence workbook",
    )
    require_unique_companies(
        frame,
        company_count,
        "Cash-flow intelligence workbook",
    )

    if frame["capital_allocation_label"].isna().any():
        raise BuildFailure(
            "Cash-flow intelligence workbook has blank "
            "capital_allocation_label values."
        )

    distress = read_csv_checked(
        DISTRESS_ALERTS_PATH,
        "Distress alerts CSV",
        allow_empty=True,
    )

    return {
        "companies": len(frame),
        "distress_alerts": len(distress),
    }


def validate_capital_allocation_outputs(
    company_count: int,
) -> dict[str, int]:
    """Validate Day 32 distribution and pattern changes."""

    distribution = normalise_columns(
        read_csv_checked(
            CAPITAL_DISTRIBUTION_PATH,
            "Capital-allocation distribution CSV",
        )
    )

    label_column = first_column(
        distribution,
        (
            "label",
            "pattern_label",
            "capital_allocation_label",
        ),
    )

    if label_column is None:
        raise BuildFailure(
            "Capital-allocation distribution CSV has no label column."
        )

    count_column = first_column(
        distribution,
        (
            "company_count",
            "count",
        ),
    )

    if count_column is None:
        raise BuildFailure(
            "Capital-allocation distribution CSV has no "
            "company_count or count column."
        )

    require_columns(
        distribution,
        (
            "percentage",
        ),
        "Capital-allocation distribution CSV",
    )

    counts = pd.to_numeric(
        distribution[count_column],
        errors="coerce",
    )

    percentages = pd.to_numeric(
        distribution["percentage"],
        errors="coerce",
    )

    if counts.isna().any() or percentages.isna().any():
        raise BuildFailure(
            "Capital-allocation distribution contains non-numeric "
            "count or percentage values."
        )

    if int(counts.sum()) != company_count:
        raise BuildFailure(
            "Capital-allocation distribution count sums to "
            f"{int(counts.sum())}; expected {company_count}."
        )

    if abs(float(percentages.sum()) - 100.0) > 0.15:
        raise BuildFailure(
            "Capital-allocation percentages sum to "
            f"{float(percentages.sum()):.4f}; expected approximately 100."
        )

    changes = normalise_columns(
        read_csv_checked(
            PATTERN_CHANGES_PATH,
            "Capital-allocation pattern changes CSV",
            allow_empty=True,
        )
    )

    require_columns(
        changes,
        (
            "company_id",
            "ticker",
            "company_name",
            "previous_year",
            "current_year",
            "previous_pattern",
            "current_pattern",
            "change_description",
        ),
        "Capital-allocation pattern changes CSV",
    )

    return {
        "distribution_rows": len(distribution),
        "pattern_changes": len(changes),
    }


def validate_tearsheets(
    company_count: int,
) -> dict[str, int]:
    """Validate Day 34 company tearsheets and skip tracking."""

    skipped = normalise_columns(
        read_csv_checked(
            SKIPPED_TEARSHEETS_PATH,
            "Skipped tearsheets CSV",
            allow_empty=True,
        )
    )

    require_columns(
        skipped,
        (
            "company_id",
            "ticker",
            "company_name",
            "available_years",
            "skip_reason",
        ),
        "Skipped tearsheets CSV",
    )

    if not skipped.empty:
        available_years = pd.to_numeric(
            skipped["available_years"],
            errors="coerce",
        )

        if available_years.isna().any():
            raise BuildFailure(
                "Skipped tearsheets CSV contains invalid available_years."
            )

        invalid_skips = skipped[
            available_years >= 3
        ]

        if not invalid_skips.empty:
            raise BuildFailure(
                "Companies with at least three years were incorrectly skipped: "
                + ", ".join(
                    invalid_skips["ticker"]
                    .astype(str)
                    .tolist()
                )
            )

    expected_pdf_count = (
        company_count - len(skipped)
    )

    pdf_paths = sorted(
        TEARSHEET_DIR.glob(
            "*_tearsheet.pdf"
        )
    )

    if len(pdf_paths) != expected_pdf_count:
        raise BuildFailure(
            f"Found {len(pdf_paths)} company tearsheets; "
            f"expected {expected_pdf_count}."
        )

    fixed_name = TEARSHEET_DIR / "_tearsheet.pdf"

    if fixed_name.exists():
        raise BuildFailure(
            "Invalid fixed tearsheet filename exists: "
            f"{fixed_name}"
        )

    small = [
        path
        for path in pdf_paths
        if path.stat().st_size
        < MINIMUM_TEARSHEET_BYTES
    ]

    if small:
        raise BuildFailure(
            "Company tearsheets below 30 KB:\n"
            + "\n".join(
                f"  {path.name}: {path.stat().st_size:,} bytes"
                for path in small
            )
        )

    PdfReader = pdf_reader_class()
    invalid_pages: list[str] = []
    blank_pages: list[str] = []

    for path in pdf_paths:
        reader = PdfReader(
            str(path)
        )

        if len(reader.pages) != 2:
            invalid_pages.append(
                f"{path.name} ({len(reader.pages)} pages)"
            )
            continue

        for page_index, page in enumerate(
            reader.pages,
            start=1,
        ):
            text = (
                page.extract_text()
                or ""
            ).strip()

            if len(text) < 20:
                blank_pages.append(
                    f"{path.name} page {page_index}"
                )

    if invalid_pages:
        raise BuildFailure(
            "Company tearsheets with invalid page counts:\n  "
            + "\n  ".join(invalid_pages)
        )

    if blank_pages:
        raise BuildFailure(
            "Possible blank company tearsheet pages:\n  "
            + "\n  ".join(blank_pages)
        )

    temp_chart_dir = OUTPUT_DIR / "temp_charts"

    if temp_chart_dir.exists() and any(
        temp_chart_dir.iterdir()
    ):
        raise BuildFailure(
            "Temporary chart files remain after tearsheet generation:\n"
            f"  {temp_chart_dir}"
        )

    return {
        "generated": len(pdf_paths),
        "skipped": len(skipped),
    }


def validate_sector_reports(
    company_count: int,
) -> dict[str, int]:
    """Validate Day 34 sector reports and approved sector coverage."""

    from src.reports.sector_report import load_company_metrics

    metrics = load_company_metrics()

    if metrics["company_id"].nunique() != company_count:
        raise BuildFailure(
            "Sector metrics company coverage does not match the database."
        )

    sector_count = int(
        metrics["sector"].nunique()
    )

    if sector_count != EXPECTED_SECTOR_COUNT:
        raise BuildFailure(
            f"Sector taxonomy contains {sector_count} sectors; "
            f"expected {EXPECTED_SECTOR_COUNT}."
        )

    pdf_paths = sorted(
        SECTOR_DIR.glob(
            "*_report.pdf"
        )
    )

    if len(pdf_paths) != EXPECTED_SECTOR_COUNT:
        raise BuildFailure(
            f"Found {len(pdf_paths)} sector PDFs; "
            f"expected {EXPECTED_SECTOR_COUNT}."
        )

    PdfReader = pdf_reader_class()
    invalid: list[str] = []

    for path in pdf_paths:
        reader = PdfReader(
            str(path)
        )

        if len(reader.pages) < 1:
            invalid.append(
                f"{path.name}: no pages"
            )
            continue

        for page_index, page in enumerate(
            reader.pages,
            start=1,
        ):
            text = (
                page.extract_text()
                or ""
            ).strip()

            if len(text) < 20:
                invalid.append(
                    f"{path.name}: page {page_index} appears blank"
                )

    if invalid:
        raise BuildFailure(
            "Invalid sector report(s):\n  "
            + "\n  ".join(invalid)
        )

    return {
        "sectors": sector_count,
        "pdfs": len(pdf_paths),
    }


def sorted_database_tickers() -> list[str]:
    """Return all company tickers sorted alphabetically."""

    with sqlite3.connect(
        DATABASE_PATH
    ) as connection:
        frame = pd.read_sql_query(
            """
            SELECT company_id
            FROM companies
            WHERE company_id IS NOT NULL
              AND TRIM(CAST(company_id AS TEXT)) <> ''
            """,
            connection,
        )

    return sorted(
        frame["company_id"]
        .astype(str)
        .str.strip()
        .str.upper()
        .drop_duplicates()
        .tolist()
    )


def validate_portfolio_summary(
    company_count: int,
) -> dict[str, int]:
    """Validate Day 35 portfolio PDF page count, content, and order."""

    require_file(
        PORTFOLIO_PATH,
        "Portfolio summary PDF",
    )

    expected_tickers = sorted_database_tickers()

    if len(expected_tickers) != company_count:
        raise BuildFailure(
            "Ticker count used for portfolio validation does not match "
            "the database company count."
        )

    PdfReader = pdf_reader_class()
    reader = PdfReader(
        str(PORTFOLIO_PATH)
    )

    if len(reader.pages) != company_count:
        raise BuildFailure(
            f"Portfolio summary contains {len(reader.pages)} pages; "
            f"expected {company_count}."
        )

    blank_pages: list[int] = []
    order_errors: list[str] = []

    for page_number, (
        page,
        expected_ticker,
    ) in enumerate(
        zip(
            reader.pages,
            expected_tickers,
            strict=True,
        ),
        start=1,
    ):
        text = (
            page.extract_text()
            or ""
        ).strip()

        if len(text) < 50:
            blank_pages.append(
                page_number
            )
            continue

        if expected_ticker not in text.upper():
            order_errors.append(
                f"page {page_number}: expected ticker {expected_ticker}"
            )

    if blank_pages:
        raise BuildFailure(
            "Possible blank portfolio page(s): "
            + ", ".join(
                str(page)
                for page in blank_pages
            )
        )

    if order_errors:
        raise BuildFailure(
            "Portfolio ticker order validation failed:\n  "
            + "\n  ".join(
                order_errors[:20]
            )
        )

    return {
        "pages": len(reader.pages),
        "bytes": PORTFOLIO_PATH.stat().st_size,
    }


def run_final_validations(
    company_count: int,
) -> None:
    """Run all final validations and print a concise summary."""

    print_heading("Step 8/8 — Final validations")

    validations: tuple[
        tuple[str, Callable[[], dict[str, int]]],
        ...,
    ] = (
        (
            "NLP parser outputs",
            validate_nlp_outputs,
        ),
        (
            "Pros and cons coverage",
            lambda: validate_pros_cons_outputs(
                company_count
            ),
        ),
        (
            "Cash-flow intelligence",
            lambda: validate_cashflow_outputs(
                company_count
            ),
        ),
        (
            "Capital-allocation outputs",
            lambda: validate_capital_allocation_outputs(
                company_count
            ),
        ),
        (
            "Company tearsheets",
            lambda: validate_tearsheets(
                company_count
            ),
        ),
        (
            "Sector reports",
            lambda: validate_sector_reports(
                company_count
            ),
        ),
        (
            "Portfolio summary",
            lambda: validate_portfolio_summary(
                company_count
            ),
        ),
    )

    summaries: list[
        tuple[str, dict[str, int]]
    ] = []

    for name, validator in validations:
        started = time.perf_counter()

        try:
            result = validator()
        except BuildFailure:
            raise
        except Exception as exc:
            raise BuildFailure(
                f"Validation failed unexpectedly: {name}\n{exc}"
            ) from exc

        elapsed = time.perf_counter() - started

        print(
            f"[PASS] {name} "
            f"({elapsed:,.1f}s)",
            flush=True,
        )

        summaries.append(
            (
                name,
                result,
            )
        )

    print()
    print("Validation summary")
    print_rule("-")

    for name, values in summaries:
        details = ", ".join(
            f"{key}={value}"
            for key, value in values.items()
        )

        print(
            f"{name}: {details}",
            flush=True,
        )

    print()
    print(
        "FINAL VALIDATION: PASS — all Sprint 5 outputs are present, "
        "readable, and meet the project exit criteria.",
        flush=True,
    )


# =============================================================================
# MAIN
# =============================================================================


def main() -> int:
    """Run the complete Sprint 5 build."""

    overall_started = time.perf_counter()

    try:
        company_count = run_preflight()

        for step in build_steps():
            run_build_step(step)

        run_final_validations(
            company_count
        )

    except BuildFailure as exc:
        print()
        print_rule("!")
        print("SPRINT 5 BUILD FAILED")
        print_rule("!")
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        print()
        print_rule("!")
        print("SPRINT 5 BUILD CANCELLED BY USER")
        print_rule("!")
        return 130
    except Exception as exc:
        print()
        print_rule("!")
        print("SPRINT 5 BUILD FAILED WITH AN UNEXPECTED ERROR")
        print_rule("!")
        print(
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 1

    elapsed = time.perf_counter() - overall_started

    print_heading("SPRINT 5 MASTER BUILD COMPLETED")
    print(
        f"Overall status: PASS\n"
        f"Elapsed time:   {elapsed:,.1f} seconds\n"
        f"Companies:      {company_count}\n"
        f"Tearsheets:     {len(list(TEARSHEET_DIR.glob('*_tearsheet.pdf')))}\n"
        f"Sector PDFs:    {len(list(SECTOR_DIR.glob('*_report.pdf')))}\n"
        f"Portfolio PDF:  {PORTFOLIO_PATH}",
        flush=True,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())