# N100 Financial Intelligence Platform — Sprint 1 Data Foundation

This starter pack implements the Sprint 1 project structure, SQLite schema, configurable Excel/CSV loader, normalisers, data-quality validation, audit files, tests, verification scripts, and Makefile/Windows batch targets.

## Important table-count clarification

The task text says **10 tables**, but the supplied list contains **11 names**:

1. companies
2. profitandloss
3. balancesheet
4. cashflow
5. analysis
6. documents
7. prosandcons
8. sectors
9. stock_prices
10. financial_ratios
11. peer_groups

Recommended Sprint 1 interpretation:

- Populate the 10 source/data-foundation tables.
- Create `financial_ratios` in the schema, but populate it during Sprint 2.

Confirm this interpretation with the project manager before final submission.

## 1. Prerequisites

Install:

- Python 3.11 or 3.12
- Git
- VS Code
- Optional: GNU Make through Git Bash, Chocolatey, Scoop, or WSL

Windows users can use `tasks.bat` instead of Make.

## 2. Create and activate the virtual environment

Open Command Prompt inside the project folder:

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 3. Configure environment variables

```bat
copy .env.example .env
```

## 4. Add the 12 source files

Place all original files inside:

```text
data/raw/
```

Do not manually edit the original source files.

## 5. Configure source-file mappings

Open:

```text
config/table_config.yml
```

For every real source file:

- Replace the placeholder filename.
- Set `type` to `excel` or `csv`.
- Set the correct Excel sheet name.
- Set the target SQLite table.
- Add column renames from source headers to standard schema names.

## 6. Run the unit tests

```bat
tasks.bat test
```

Expected:

```text
35 passed
```

## 7. Run the ETL pipeline

```bat
tasks.bat load
```

The pipeline creates `nifty100.db`, loads the configured data, applies normalisation, records rejected rows, runs DQ-01 to DQ-16, and generates the audit files.

## 8. Verify the database

```bat
tasks.bat verify
```

Target results:

```text
companies: 92
foreign_key_violations: 0
critical_validation_failures: 0
```

## 9. Generate the Sprint report

```bat
tasks.bat report
```

## 10. Run exploratory SQL

Open `nifty100.db` in DB Browser for SQLite, DBeaver, or VS Code SQLite and run:

```text
notebooks/exploratory_queries.sql
```

## 11. Manual review

Review five companies from different sectors and complete:

```text
output/manual_review.md
```

## 12. Final exit criteria

- `SELECT COUNT(*) FROM companies;` returns 92.
- `PRAGMA foreign_key_check;` returns zero rows.
- `load_audit.csv` has no unresolved critical rejection.
- `validation_failures.csv` has no unresolved CRITICAL failure.
- 35+ tests pass.
- Five-company manual review is complete.
- Sprint review is signed off.

## 13. Git commands

```bat
git init
git add .
git commit -m "Sprint 1: data foundation setup"
```

After successful loading:

```bat
git add .
git commit -m "Sprint 1: ETL, validation and SQLite database complete"
```

## Deliverables

```text
nifty100.db
output/load_audit.csv
output/validation_failures.csv
output/rejected_rows.csv
output/manual_review.md
output/sprint1_report.md
src/etl/loader.py
src/etl/validator.py
src/etl/normaliser.py
db/schema.sql
tests/etl/test_normaliser.py
notebooks/exploratory_queries.sql
```

---

## Sprint 2 — Financial Ratio Engine

### Completed Work

- Processed all 92 N100 companies
- Generated 1,073 valid company-year ratio records
- Populated 55 financial-ratio and supporting columns
- Implemented profitability, leverage and efficiency ratios
- Implemented Revenue, PAT and EPS CAGR for 3Y, 5Y and 10Y
- Implemented cash-flow quality and capital-allocation classification
- Applied the Financials-sector leverage carve-out
- Generated screener and edge-case outputs

### Verification

- KPI tests: 36 passed
- Complete project tests: 71 passed
- Manual ROE and Revenue CAGR checks: 3 passed
- Screener results: 38 companies
- Foreign-key violations: 0
- Critical validation failures: 0

### Source Data Notes

- The source contains 1,073 valid unique company-year combinations instead of the stated 1,100+.
- The supplied sector file classifies 23 companies as Financials instead of 19.
- BEL's extreme computed ROE matches the supplied equity values and is documented as a source-data issue.
- No duplicate or synthetic records were introduced.

### Sprint 2 Deliverables

- `src/analytics/ratios.py`
- `src/analytics/cagr.py`
- `src/analytics/cashflow_kpis.py`
- `src/analytics/engine.py`
- `tests/kpi/`
- `output/capital_allocation.csv`
- `output/ratio_edge_cases.log`
- `output/screener_preview.csv`
- `output/manual_ratio_spot_check.csv`
- `output/sprint2_retrospective.md`

### Status

Sprint 2 technical implementation completed and submitted for review.