from __future__ import annotations

import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.analytics.peer import (
    build_peer_dataset,
    calculate_peer_percentiles,
)
from src.screener.engine import (
    DEFAULT_CONFIG,
    load_config,
    load_financial_data,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "reports" / "radar_charts"


RADAR_AXES = [
    ("ROE", "roe_percentile"),
    ("ROCE", "roce_percentile"),
    ("NPM", "net_profit_margin_percentile"),
    ("D/E", "debt_to_equity_percentile"),
    ("FCF Score", "free_cash_flow_percentile"),
    ("PAT CAGR 5Y", "pat_cagr_5yr_percentile"),
    ("Revenue CAGR 5Y", "revenue_cagr_5yr_percentile"),
    ("Composite Score", "composite_quality_score"),
]


def safe_filename(value: str) -> str:
    """Return a Windows-safe filename component."""

    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", str(value).strip())
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned.strip("._") or "company"


def close_polygon(values: list[float]) -> list[float]:
    """Repeat the first value to close a radar polygon."""

    if not values:
        return []

    return [*values, values[0]]


def radar_angles(axis_count: int) -> np.ndarray:
    """Return equally spaced polar angles with the first angle repeated."""

    angles = np.linspace(
        0,
        2 * np.pi,
        axis_count,
        endpoint=False,
    )

    return np.concatenate([angles, angles[:1]])


def get_radar_values(
    row: pd.Series,
) -> list[float]:
    """Return eight normalised 0–100 radar values."""

    values: list[float] = []

    for _, column in RADAR_AXES:
        value = pd.to_numeric(
            pd.Series([row.get(column)]),
            errors="coerce",
        ).iloc[0]

        if pd.isna(value):
            score = 50.0
        elif column.endswith("_percentile"):
            score = float(value) * 100
        else:
            score = float(value)

        values.append(
            float(np.clip(score, 0, 100))
        )

    return values


def generate_peer_radar_chart(
    company_row: pd.Series,
    peer_group_data: pd.DataFrame,
    output_directory: Path,
) -> Path:
    """Generate one company-versus-peer-average radar chart."""

    labels = [
        label
        for label, _ in RADAR_AXES
    ]

    company_values = get_radar_values(
        company_row
    )

    peer_average_row = pd.Series(
        {
            column: peer_group_data[column].mean()
            for _, column in RADAR_AXES
        }
    )

    peer_values = get_radar_values(
        peer_average_row
    )

    angles = radar_angles(len(labels))
    company_polygon = close_polygon(company_values)
    peer_polygon = close_polygon(peer_values)

    figure, axis = plt.subplots(
        figsize=(9, 8),
        subplot_kw={"polar": True},
    )

    axis.plot(
        angles,
        company_polygon,
        linewidth=2.2,
        label=str(company_row["company_id"]),
    )
    axis.fill(
        angles,
        company_polygon,
        alpha=0.25,
    )

    axis.plot(
        angles,
        peer_polygon,
        linewidth=2,
        linestyle="--",
        label="Peer Group Average",
    )

    axis.set_xticks(angles[:-1])
    axis.set_xticklabels(
        labels,
        fontsize=10,
    )
    axis.set_ylim(0, 100)
    axis.set_yticks([20, 40, 60, 80, 100])
    axis.set_yticklabels(
        ["20", "40", "60", "80", "100"],
        fontsize=8,
    )
    axis.set_title(
        (
            f"{company_row['company_name']}\n"
            f"{company_row['peer_group_name']} Peer Comparison"
        ),
        fontsize=14,
        pad=24,
    )
    axis.legend(
        loc="upper right",
        bbox_to_anchor=(1.30, 1.15),
        frameon=False,
    )

    figure.tight_layout()

    filename = (
        f"{safe_filename(company_row['company_id'])}_radar.png"
    )
    output_path = output_directory / filename

    figure.savefig(
        output_path,
        dpi=180,
        bbox_inches="tight",
    )
    plt.close(figure)

    return output_path


def generate_unassigned_chart(
    company_row: pd.Series,
    nifty_average_score: float,
    output_directory: Path,
) -> Path:
    """
    Generate the required standalone chart for an unassigned company.

    Composite quality score is used as the single comparable metric.
    """

    company_score = pd.to_numeric(
        pd.Series(
            [company_row.get("composite_quality_score")]
        ),
        errors="coerce",
    ).iloc[0]

    if pd.isna(company_score):
        company_score = 50.0

    values = [
        float(np.clip(company_score, 0, 100)),
        float(np.clip(nifty_average_score, 0, 100)),
    ]

    figure, axis = plt.subplots(
        figsize=(8, 5),
    )

    bars = axis.barh(
        [
            str(company_row["company_id"]),
            "Nifty 100 Average",
        ],
        values,
    )

    axis.set_xlim(0, 100)
    axis.set_xlabel("Composite Quality Score")
    axis.set_title(
        (
            f"{company_row['company_name']}\n"
            "No Peer Group Assigned"
        ),
        fontsize=13,
    )
    axis.grid(
        axis="x",
        alpha=0.25,
    )

    for bar, value in zip(bars, values):
        axis.text(
            min(value + 1.5, 96),
            bar.get_y() + bar.get_height() / 2,
            f"{value:.2f}",
            va="center",
            fontsize=10,
        )

    figure.tight_layout()

    filename = (
        f"{safe_filename(company_row['company_id'])}_radar.png"
    )
    output_path = output_directory / filename

    figure.savefig(
        output_path,
        dpi=180,
        bbox_inches="tight",
    )
    plt.close(figure)

    return output_path


def clear_old_png_files(
    output_directory: Path,
) -> None:
    """Remove previously generated radar PNG files."""

    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    for path in output_directory.glob("*.png"):
        path.unlink()


def generate_all_charts(
    config_path: str | Path = DEFAULT_CONFIG,
    output_directory: str | Path = OUTPUT_DIR,
) -> dict[str, int]:
    """Generate charts for all companies in the latest financial universe."""

    output_directory = Path(output_directory)

    if not output_directory.is_absolute():
        output_directory = PROJECT_ROOT / output_directory

    clear_old_png_files(
        output_directory
    )

    config = load_config(config_path)
    all_financial_data = load_financial_data(config)

    peer_data, _ = build_peer_dataset(
        config_path
    )
    wide_peer_data, _ = calculate_peer_percentiles(
        peer_data
    )

    assigned_company_ids = set(
        wide_peer_data["company_id"]
        .astype(str)
        .str.strip()
    )

    peer_chart_count = 0

    for peer_group_name, group_data in wide_peer_data.groupby(
        "peer_group_name",
        sort=True,
    ):
        group_data = group_data.reset_index(drop=True)

        for _, company_row in group_data.iterrows():
            generate_peer_radar_chart(
                company_row,
                group_data,
                output_directory,
            )
            peer_chart_count += 1

    nifty_average_score = float(
        all_financial_data[
            "composite_quality_score"
        ].mean()
    )

    unassigned_data = all_financial_data[
        ~all_financial_data["company_id"]
        .astype(str)
        .str.strip()
        .isin(assigned_company_ids)
    ].copy()

    unassigned_chart_count = 0

    for _, company_row in unassigned_data.iterrows():
        generate_unassigned_chart(
            company_row,
            nifty_average_score,
            output_directory,
        )
        unassigned_chart_count += 1

    total_count = (
        peer_chart_count
        + unassigned_chart_count
    )

    expected_count = all_financial_data[
        "company_id"
    ].nunique()

    if total_count != expected_count:
        raise AssertionError(
            f"Expected {expected_count} charts, "
            f"generated {total_count}."
        )

    actual_file_count = len(
        list(output_directory.glob("*.png"))
    )

    if actual_file_count != expected_count:
        raise AssertionError(
            f"Expected {expected_count} PNG files, "
            f"found {actual_file_count}."
        )

    return {
        "peer_charts": peer_chart_count,
        "unassigned_charts": unassigned_chart_count,
        "total_charts": total_count,
    }


def main() -> None:
    counts = generate_all_charts()

    print("Radar chart generation completed")
    print("===============================")
    print(
        f"Peer-group radar charts: "
        f"{counts['peer_charts']}"
    )
    print(
        f"Unassigned standalone charts: "
        f"{counts['unassigned_charts']}"
    )
    print(
        f"Total PNG charts: "
        f"{counts['total_charts']}"
    )
    print(f"Output directory: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
