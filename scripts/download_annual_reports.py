"""Download annual-report PDFs from SQLite using parallel workers."""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Final

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# PROJECT IMPORT SETUP
# ============================================================

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

# Allows the script to run without manually setting PYTHONPATH.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_settings  # noqa: E402


# ============================================================
# PATH CONFIGURATION
# ============================================================

CACHE_FOLDER: Final[Path] = (
    PROJECT_ROOT
    / "reports"
    / "annual_reports"
    / "cache"
)

LOG_FILE: Final[Path] = (
    PROJECT_ROOT
    / "reports"
    / "annual_reports"
    / "download_log.csv"
)

INVALID_URL_VALUES: Final[set[str]] = {
    "",
    "-",
    "na",
    "n/a",
    "nan",
    "none",
    "null",
}

# More than eight workers may cause BSE connection resets.
MAX_WORKERS: Final[int] = 8

# Larger chunks improve download speed.
CHUNK_SIZE: Final[int] = 256 * 1024


# ============================================================
# URL AND FILE HELPERS
# ============================================================

def normalize_url(url: str) -> str:
    """Return a cleaned URL containing an HTTP scheme."""

    cleaned_url = str(url).strip()

    if cleaned_url.startswith("//"):
        return f"https:{cleaned_url}"

    if not cleaned_url.lower().startswith(
        ("http://", "https://")
    ):
        return f"https://{cleaned_url.lstrip('/')}"

    return cleaned_url


def is_usable_url(url: str) -> bool:
    """Check whether a database value looks like a usable URL."""

    cleaned_url = str(url).strip()

    if cleaned_url.lower() in INVALID_URL_VALUES:
        return False

    return len(cleaned_url) > 10


def safe_filename(company_id: str, year: int) -> str:
    """Create a Windows-safe annual-report filename."""

    cleaned_company = re.sub(
        r'[<>:"/\\|?*]',
        "_",
        company_id.strip().upper(),
    )

    return f"{cleaned_company}_{year}.pdf"


def is_valid_pdf(file_path: Path) -> bool:
    """Check whether a local file appears to be a valid PDF."""

    try:
        if not file_path.is_file():
            return False

        if file_path.stat().st_size == 0:
            return False

        with file_path.open("rb") as pdf_file:
            header = pdf_file.read(2048)

        return b"%PDF" in header

    except OSError:
        return False


# ============================================================
# DATABASE ACCESS
# ============================================================

def get_report_records(
    company: str | None = None,
    year: int | None = None,
) -> list[tuple[str, int, str]]:
    """Read unique and valid annual-report records from SQLite."""

    database_path = get_settings().database_path

    query = """
        SELECT
            UPPER(TRIM(d.company_id)) AS company_id,
            CAST(d.year AS INTEGER) AS report_year,
            TRIM(d.annual_report) AS report_url
        FROM documents d
        INNER JOIN companies c
            ON UPPER(TRIM(d.company_id))
             = UPPER(TRIM(c.company_id))
        WHERE d.company_id IS NOT NULL
          AND TRIM(d.company_id) <> ''
          AND d.year IS NOT NULL
          AND d.annual_report IS NOT NULL
          AND TRIM(d.annual_report) <> ''
          AND LOWER(TRIM(d.annual_report)) NOT IN (
              'null',
              'none',
              'nan',
              'na',
              'n/a',
              '-'
          )
          AND LENGTH(TRIM(d.annual_report)) > 10
    """

    parameters: list[object] = []

    if company:
        query += """
            AND UPPER(TRIM(d.company_id)) = ?
        """
        parameters.append(company.strip().upper())

    if year is not None:
        query += """
            AND CAST(d.year AS INTEGER) = ?
        """
        parameters.append(year)

    query += """
        ORDER BY
            UPPER(TRIM(d.company_id)),
            CAST(d.year AS INTEGER) DESC
    """

    with sqlite3.connect(database_path) as connection:
        raw_records = connection.execute(
            query,
            parameters,
        ).fetchall()

    # Prevent two URLs from writing to the same company/year file.
    unique_records: dict[
        tuple[str, int],
        tuple[str, int, str],
    ] = {}

    for company_id, report_year, report_url in raw_records:
        company_text = str(company_id).strip().upper()
        year_value = int(report_year)
        url_text = str(report_url).strip()

        if not is_usable_url(url_text):
            continue

        key = (
            company_text,
            year_value,
        )

        unique_records.setdefault(
            key,
            (
                company_text,
                year_value,
                url_text,
            ),
        )

    return list(unique_records.values())


# ============================================================
# HTTP SESSION
# ============================================================

def create_session() -> requests.Session:
    """Create one reusable HTTP session for a worker thread."""

    retry_strategy = Retry(
        total=1,
        connect=1,
        read=1,
        status=1,
        backoff_factor=0.25,
        status_forcelist=[
            429,
            500,
            502,
            503,
            504,
        ],
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=2,
        pool_maxsize=2,
    )

    session = requests.Session()

    session.mount(
        "https://",
        adapter,
    )

    session.mount(
        "http://",
        adapter,
    )

    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/150.0 Safari/537.36"
            ),
            "Referer": "https://www.bseindia.com/",
            "Accept": (
                "application/pdf,"
                "application/octet-stream;q=0.9,"
                "*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        }
    )

    return session


_THREAD_LOCAL = threading.local()


def get_worker_session() -> requests.Session:
    """Return one persistent session for each worker thread."""

    session = getattr(
        _THREAD_LOCAL,
        "session",
        None,
    )

    if session is None:
        session = create_session()
        _THREAD_LOCAL.session = session

    return session


# ============================================================
# PDF DOWNLOADER
# ============================================================

def download_report(
    session: requests.Session,
    company_id: str,
    year: int,
    url: str,
    replace: bool = False,
    attempts: int = 1,
) -> tuple[str, str]:
    """Download and validate one annual-report PDF."""

    normalized_url = normalize_url(url)

    filename = safe_filename(
        company_id,
        year,
    )

    destination = CACHE_FOLDER / filename

    temporary_file = destination.with_suffix(
        ".pdf.part"
    )

    # Skip an existing valid report.
    if destination.exists() and not replace:
        if is_valid_pdf(destination):
            return "SKIPPED", "File already exists"

        destination.unlink(missing_ok=True)

    last_error = "Unknown download error"
    total_attempts = max(1, attempts)

    for attempt_number in range(
        1,
        total_attempts + 1,
    ):
        temporary_file.unlink(missing_ok=True)

        try:
            with session.get(
                normalized_url,
                stream=True,
                timeout=(15, 180),
                allow_redirects=True,
            ) as response:
                response.raise_for_status()

                chunks = response.iter_content(
                    chunk_size=CHUNK_SIZE,
                )

                first_chunk = next(
                    chunks,
                    b"",
                )

                # Prevent HTML error pages from being saved as PDFs.
                if b"%PDF" not in first_chunk[:2048]:
                    content_type = response.headers.get(
                        "Content-Type",
                        "unknown",
                    )

                    temporary_file.unlink(
                        missing_ok=True
                    )

                    return (
                        "FAILED",
                        "Response is not a PDF; "
                        f"content type={content_type}",
                    )

                with temporary_file.open(
                    "wb"
                ) as pdf_file:
                    pdf_file.write(first_chunk)

                    for chunk in chunks:
                        if chunk:
                            pdf_file.write(chunk)

            if not is_valid_pdf(temporary_file):
                temporary_file.unlink(
                    missing_ok=True
                )

                last_error = (
                    "Downloaded content failed "
                    "PDF validation"
                )

            else:
                temporary_file.replace(
                    destination
                )

                size_mb = (
                    destination.stat().st_size
                    / 1_048_576
                )

                return (
                    "DOWNLOADED",
                    f"{size_mb:.2f} MB",
                )

        except requests.RequestException as error:
            temporary_file.unlink(
                missing_ok=True
            )

            last_error = (
                f"Attempt "
                f"{attempt_number}/{total_attempts}: "
                f"{error}"
            )

        except OSError as error:
            temporary_file.unlink(
                missing_ok=True
            )

            last_error = (
                f"Attempt "
                f"{attempt_number}/{total_attempts}: "
                f"{error}"
            )

        if attempt_number < total_attempts:
            retry_wait = min(
                2 ** (attempt_number - 1),
                5,
            )

            time.sleep(retry_wait)

    return "FAILED", last_error


def process_report(
    record: tuple[str, int, str],
    replace: bool,
    attempts: int,
    delay: float,
) -> tuple[str, int, str, str, str]:
    """Download one report using a worker thread."""

    company_id, report_year, report_url = record

    session = get_worker_session()

    status, message = download_report(
        session=session,
        company_id=company_id,
        year=report_year,
        url=report_url,
        replace=replace,
        attempts=attempts,
    )

    if delay > 0:
        time.sleep(delay)

    return (
        company_id,
        report_year,
        report_url,
        status,
        message,
    )


# ============================================================
# DOWNLOAD LOG
# ============================================================

def write_log(
    rows: list[list[object]],
) -> None:
    """Write the complete download log."""

    LOG_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    rows.sort(
        key=lambda row: (
            str(row[0]),
            -int(row[1]),
        )
    )

    with LOG_FILE.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as log_file:
        writer = csv.writer(log_file)

        writer.writerow(
            [
                "company_id",
                "year",
                "filename",
                "status",
                "message",
                "annual_report_url",
            ]
        )

        writer.writerows(rows)


# ============================================================
# COMMAND-LINE APPLICATION
# ============================================================

def main() -> None:
    """Run the parallel annual-report downloader."""

    parser = argparse.ArgumentParser(
        description=(
            "Download annual-report PDFs "
            "into the local cache."
        )
    )

    parser.add_argument(
        "--company",
        help=(
            "Download one company, "
            "for example TCS."
        ),
    )

    parser.add_argument(
        "--year",
        type=int,
        help=(
            "Download one year, "
            "for example 2024."
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        help=(
            "Maximum number of records "
            "to process."
        ),
    )

    parser.add_argument(
        "--replace",
        action="store_true",
        help=(
            "Replace valid PDFs that "
            "already exist."
        ),
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=6,
        help=(
            "Parallel workers. "
            "Recommended: 4 to 8. "
            "Default: 6."
        ),
    )

    parser.add_argument(
        "--attempts",
        type=int,
        default=1,
        help=(
            "Complete attempts per missing report. "
            "Default: 1."
        ),
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help=(
            "Optional delay after each worker "
            "download. Default: 0."
        ),
    )

    arguments = parser.parse_args()

    CACHE_FOLDER.mkdir(
        parents=True,
        exist_ok=True,
    )

    records = get_report_records(
        company=arguments.company,
        year=arguments.year,
    )

    if arguments.limit is not None:
        records = records[
            : max(arguments.limit, 0)
        ]

    if not records:
        print(
            "No matching annual-report "
            "records were found."
        )
        return

    workers = max(
        1,
        min(
            arguments.workers,
            MAX_WORKERS,
        ),
    )

    attempts = max(
        1,
        arguments.attempts,
    )

    delay = max(
        0.0,
        arguments.delay,
    )

    log_rows: list[list[object]] = []

    pending_records: list[
        tuple[str, int, str]
    ] = []

    # Skip existing files before starting worker threads.
    for company_id, report_year, report_url in records:
        filename = safe_filename(
            company_id,
            report_year,
        )

        destination = CACHE_FOLDER / filename

        if destination.exists() and not arguments.replace:
            if is_valid_pdf(destination):
                log_rows.append(
                    [
                        company_id,
                        report_year,
                        filename,
                        "SKIPPED",
                        "File already exists",
                        normalize_url(report_url),
                    ]
                )

                continue

            destination.unlink(
                missing_ok=True
            )

        pending_records.append(
            (
                company_id,
                report_year,
                report_url,
            )
        )

    skipped = len(log_rows)
    downloaded = 0
    failed = 0
    completed = skipped
    total = len(records)

    print(f"Reports selected : {total}")
    print(f"Already cached   : {skipped}")
    print(f"To download      : {len(pending_records)}")
    print(f"Parallel workers : {workers}")
    print(f"Destination      : {CACHE_FOLDER}")
    print("=" * 78)

    if not pending_records:
        write_log(log_rows)

        print(
            "All selected annual reports "
            "are already downloaded."
        )

        print(f"Log file: {LOG_FILE}")
        return

    executor = ThreadPoolExecutor(
        max_workers=workers
    )

    future_map: dict[
        Future[
            tuple[str, int, str, str, str]
        ],
        tuple[str, int, str],
    ] = {
        executor.submit(
            process_report,
            record,
            arguments.replace,
            attempts,
            delay,
        ): record
        for record in pending_records
    }

    interrupted = False

    try:
        for future in as_completed(future_map):
            record = future_map[future]

            try:
                (
                    company_id,
                    report_year,
                    report_url,
                    status,
                    message,
                ) = future.result()

            except Exception as error:
                (
                    company_id,
                    report_year,
                    report_url,
                ) = record

                status = "FAILED"
                message = f"Worker error: {error}"

            filename = safe_filename(
                company_id,
                report_year,
            )

            completed += 1

            if status == "DOWNLOADED":
                downloaded += 1

            elif status == "SKIPPED":
                skipped += 1

            else:
                failed += 1

            print(
                f"[{completed}/{total}] "
                f"{filename:<32} "
                f"{status:<10} "
                f"{message}"
            )

            log_rows.append(
                [
                    company_id,
                    report_year,
                    filename,
                    status,
                    message,
                    normalize_url(report_url),
                ]
            )

    except KeyboardInterrupt:
        interrupted = True

        print(
            "\nDownload interrupted. "
            "Cancelling pending tasks..."
        )

        for future in future_map:
            future.cancel()

    finally:
        executor.shutdown(
            wait=not interrupted,
            cancel_futures=interrupted,
        )

        write_log(log_rows)

    print("=" * 78)
    print("Annual-report download completed")
    print(f"Downloaded : {downloaded}")
    print(f"Skipped    : {skipped}")
    print(f"Failed     : {failed}")
    print(f"Processed  : {len(log_rows)} of {total}")
    print(f"Log file   : {LOG_FILE}")


if __name__ == "__main__":
    main()