# Sprint 2 Retrospective — Financial Ratio Engine

## Completed

- Calculated profitability, leverage, efficiency and cash-flow KPIs.
- Calculated Revenue, PAT and EPS CAGR for 3Y, 5Y and 10Y.
- Added all required CAGR edge-case flags.
- Added Financials-sector leverage carve-out.
- Generated capital allocation and screener outputs.
- Populated 55 columns for 1,073 valid company-year records.
- Completed 36 KPI tests and 71 total tests with zero failures.
- Completed manual ROE and Revenue CAGR checks for BEL, HDFCBANK and TCS with zero difference.

## Formula Decisions

- EBIT = Profit Before Tax + Interest.
- ROE = Net Profit / (Equity Capital + Reserves) × 100.
- FCF = Operating Activity + Investing Activity.
- CapEx proxy = Absolute Investing Activity.
- Exact year-minus-window records are required for CAGR.
- Computed ROE and ROCE are used for analytics.

## Source Limitations

- The source contains 1,073 valid unique company-year combinations instead of 1,100+.
- The source classifies 23 companies as Financials instead of 19.
- BEL’s extreme ROE matches the supplied equity values and is documented as a source-data issue.
- Nineteen company-year records have insufficient cash-flow data.
- No duplicate or synthetic records were added.


## Sprint 2 — Financial Ratio Engine

- 92 companies processed
- 1,073 valid company-year rows
- 55 financial-ratio columns
- 36 KPI tests and 71 total tests passed
- 38 companies returned by the quality screener
- Capital-allocation and edge-case outputs generated
- Manual ROE and 5Y Revenue CAGR verification completed
- Source row-count and Financials-count differences documented