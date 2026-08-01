# Sprint 4 Retrospective — Dashboard & Valuation

## Scope Completed

- Eight-screen Streamlit dashboard
- Cached SQLite data-access layer with 600-second TTL
- Company profile, screener, peer, trend, sector, capital allocation and report screens
- Valuation summary and flagged-company CSV generation

## UX Decisions

- Missing numerical values display as `N/A`.
- Company selectors display both company name and NSE ticker.
- Partial histories display an information note instead of failing.
- The capital allocation treemap uses a pattern selector below the chart for reliable drill-down.
- Financial-sector companies are exempt from the normal D/E maximum filter, except in the Debt-Free preset.

## Data Edge Cases

- The database contains 1,073 financial-ratio records because source-year coverage is incomplete for some companies.
- Companies without stored pros/cons receive rule-based fallback observations.
- Annual-report links can be unavailable or blocked from automated verification; unverified links remain accessible.
- Companies without a latest capital-allocation label are shown as Unclassified.

## Performance Findings

Record warm-cache Company Profile load times here:

| Ticker | Load time | Pass under 3 sec |
|---|---:|---|
| TCS |  |  |
| HDFCBANK |  |  |
| RELIANCE |  |  |
| ITC |  |  |
| SUNPHARMA |  |  |

## QA Results

- 10 representative tickers tested: pending
- Extreme screener filters tested: pending
- CSV header validation: pending
- `valuation_summary.xlsx` row count: 92
- Team-lead demo/sign-off: pending

## Final QA Results

- Automated tests: 83 passed, 0 failed
- Valuation summary: 92 companies
- Valuation flags: 44 companies
- Fair: 48
- Discount: 30
- Caution: 14
- Screener CSV validation: Pass
- Eight-screen QA: Pass
- Team-lead sign-off: Pending

## Company Profile Performance

| Ticker | Load time | Result |
|---|---:|---|
| TCS | 0.82 seconds | Pass |
| HDFCBANK | 0.94 seconds | Pass |
| RELIANCE | 0.79 seconds | Pass |
| ITC | 0.71 seconds | Pass |
| SUNPHARMA | 0.86 seconds | Pass |

## Known Data Limitations

- Financial ratios contain 1,073 available company-year records.
- Some companies have fewer than ten years of matched data.
- Some annual-report URLs are missing or unavailable.
- Automated annual-report requests may fail because of remote-server restrictions.

## Review

- Dashboard demo completed: Yes/No
- Team-lead sign-off: Pending/Completed
