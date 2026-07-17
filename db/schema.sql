PRAGMA foreign_keys = ON;


-- =========================================================
-- 1. SECTORS
-- =========================================================

CREATE TABLE IF NOT EXISTS sectors (
    sector_id INTEGER PRIMARY KEY,
    sector_name TEXT NOT NULL UNIQUE
);


-- =========================================================
-- 2. COMPANIES
-- Source: companies.xlsx
-- =========================================================

CREATE TABLE IF NOT EXISTS companies (
    company_id TEXT PRIMARY KEY,
    company_logo TEXT,
    company_name TEXT NOT NULL,
    chart_link TEXT,
    about_company TEXT,
    website TEXT,
    nse_profile TEXT,
    bse_profile TEXT,
    face_value REAL,
    book_value REAL,
    roce_percentage REAL,
    roe_percentage REAL
);


-- =========================================================
-- 3. PROFIT AND LOSS
-- Source: profitandloss.xlsx
-- =========================================================

CREATE TABLE IF NOT EXISTS profitandloss (
    company_id TEXT NOT NULL,
    year INTEGER NOT NULL,
    sales REAL,
    expenses REAL,
    operating_profit REAL,
    opm_percentage REAL,
    other_income REAL,
    interest REAL,
    depreciation REAL,
    profit_before_tax REAL,
    tax_percentage REAL,
    net_profit REAL,
    eps REAL,
    dividend_payout REAL,

    PRIMARY KEY (company_id, year),

    FOREIGN KEY (company_id)
        REFERENCES companies(company_id)
);


-- =========================================================
-- 4. BALANCE SHEET
-- Source: balancesheet.xlsx
-- =========================================================

CREATE TABLE IF NOT EXISTS balancesheet (
    company_id TEXT NOT NULL,
    year INTEGER NOT NULL,
    equity_capital REAL,
    reserves REAL,
    borrowings REAL,
    other_liabilities REAL,
    total_liabilities REAL,
    fixed_assets REAL,
    cwip REAL,
    investments REAL,
    other_asset REAL,
    total_assets REAL,

    PRIMARY KEY (company_id, year),

    FOREIGN KEY (company_id)
        REFERENCES companies(company_id)
);


-- =========================================================
-- 5. CASH FLOW
-- Source: cashflow.xlsx
-- =========================================================

CREATE TABLE IF NOT EXISTS cashflow (
    company_id TEXT NOT NULL,
    year INTEGER NOT NULL,
    operating_activity REAL,
    investing_activity REAL,
    financing_activity REAL,
    net_cash_flow REAL,

    PRIMARY KEY (company_id, year),

    FOREIGN KEY (company_id)
        REFERENCES companies(company_id)
);


-- =========================================================
-- 6. ANALYSIS
-- Source: analysis.xlsx
-- =========================================================

CREATE TABLE IF NOT EXISTS analysis (
    id INTEGER PRIMARY KEY,
    company_id TEXT NOT NULL,
    compounded_sales_growth TEXT,
    compounded_profit_growth TEXT,
    stock_price_cagr TEXT,
    roe TEXT,

    FOREIGN KEY (company_id)
        REFERENCES companies(company_id)
);

-- =========================================================
-- 7. DOCUMENTS
-- Source: documents.xlsx
-- =========================================================

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    company_id TEXT NOT NULL,
    year INTEGER,
    annual_report TEXT,

    FOREIGN KEY (company_id)
        REFERENCES companies(company_id)
);


-- =========================================================
-- 8. PROS AND CONS
-- Source: prosandcons.xlsx
-- =========================================================

CREATE TABLE IF NOT EXISTS prosandcons (
    id INTEGER PRIMARY KEY,
    company_id TEXT NOT NULL,
    pros TEXT,
    cons TEXT,

    FOREIGN KEY (company_id)
        REFERENCES companies(company_id)
);


-- =========================================================
-- 9. STOCK PRICES
-- Source: stock_prices.xlsx
-- =========================================================

CREATE TABLE IF NOT EXISTS stock_prices (
    id INTEGER,
    company_id TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL NOT NULL,
    volume REAL,
    adjusted_close REAL,

    PRIMARY KEY (company_id, date),

    FOREIGN KEY (company_id)
        REFERENCES companies(company_id)
);

-- =========================================================
-- 10. PEER GROUPS
-- Source: peer_groups.xlsx
-- =========================================================

CREATE TABLE IF NOT EXISTS peer_groups (
    id INTEGER PRIMARY KEY,
    peer_group_name TEXT NOT NULL,
    company_id TEXT NOT NULL,
    is_benchmark INTEGER,

    FOREIGN KEY (company_id)
        REFERENCES companies(company_id)
);


-- =========================================================
-- 11. FINANCIAL RATIOS
-- Source: financial_ratios.xlsx
-- =========================================================

CREATE TABLE IF NOT EXISTS financial_ratios (
    id INTEGER,
    company_id TEXT NOT NULL,
    year INTEGER NOT NULL,
    net_profit_margin_pct REAL,
    operating_profit_margin_pct REAL,
    return_on_equity_pct REAL,
    debt_to_equity REAL,
    interest_coverage REAL,
    asset_turnover REAL,
    free_cash_flow_cr REAL,
    capex_cr REAL,
    earnings_per_share REAL,
    book_value_per_share REAL,
    dividend_payout_ratio_pct REAL,
    total_debt_cr REAL,
    cash_from_operations_cr REAL,

    PRIMARY KEY (company_id, year),

    FOREIGN KEY (company_id)
        REFERENCES companies(company_id)
);


-- =========================================================
-- 12. MARKET CAP
-- Source: market_cap.xlsx
-- =========================================================

CREATE TABLE IF NOT EXISTS market_cap (
    id INTEGER,
    company_id TEXT NOT NULL,
    year INTEGER NOT NULL,
    market_cap_crore REAL,
    enterprise_value_crore REAL,
    pe_ratio REAL,
    pb_ratio REAL,
    ev_ebitda REAL,
    dividend_yield_pct REAL,

    PRIMARY KEY (company_id, year),

    FOREIGN KEY (company_id)
        REFERENCES companies(company_id)
);


-- =========================================================
-- INDEXES
-- =========================================================

CREATE INDEX IF NOT EXISTS idx_pnl_year
    ON profitandloss(year);

CREATE INDEX IF NOT EXISTS idx_bs_year
    ON balancesheet(year);

CREATE INDEX IF NOT EXISTS idx_cf_year
    ON cashflow(year);

CREATE INDEX IF NOT EXISTS idx_price_date
    ON stock_prices(date);

CREATE INDEX IF NOT EXISTS idx_documents_company
    ON documents(company_id);

CREATE INDEX IF NOT EXISTS idx_peer_groups_company
    ON peer_groups(company_id);

CREATE INDEX IF NOT EXISTS idx_ratios_year
    ON financial_ratios(year);

CREATE INDEX IF NOT EXISTS idx_market_cap_year
    ON market_cap(year);