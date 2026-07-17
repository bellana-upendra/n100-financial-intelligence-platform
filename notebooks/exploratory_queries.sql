-- 1
SELECT COUNT(*) AS company_count FROM companies;

-- 2
SELECT s.sector_name, COUNT(*) AS company_count
FROM companies c
LEFT JOIN sectors s ON s.sector_id = c.sector_id
GROUP BY s.sector_name
ORDER BY company_count DESC;

-- 3
SELECT c.company_name, MIN(p.year) AS first_year, MAX(p.year) AS last_year,
       COUNT(DISTINCT p.year) AS year_count
FROM companies c
JOIN profitandloss p ON p.company_id = c.company_id
GROUP BY c.company_id, c.company_name
ORDER BY year_count, c.company_name;

-- 4
WITH latest AS (
    SELECT company_id, MAX(year) AS max_year
    FROM profitandloss GROUP BY company_id
)
SELECT c.company_name, p.year, p.sales
FROM profitandloss p
JOIN latest l ON l.company_id = p.company_id AND l.max_year = p.year
JOIN companies c ON c.company_id = p.company_id
ORDER BY p.sales DESC LIMIT 10;

-- 5
WITH latest AS (
    SELECT company_id, MAX(year) AS max_year
    FROM profitandloss GROUP BY company_id
)
SELECT c.company_name, p.year, p.opm_percent
FROM profitandloss p
JOIN latest l ON l.company_id = p.company_id AND l.max_year = p.year
JOIN companies c ON c.company_id = p.company_id
ORDER BY p.opm_percent DESC LIMIT 10;

-- 6
SELECT c.company_name, b.year, b.total_assets, b.total_liabilities,
       ABS(b.total_assets - b.total_liabilities) AS difference
FROM balancesheet b
JOIN companies c ON c.company_id = b.company_id
WHERE ABS(b.total_assets - b.total_liabilities)
      > 0.01 * MAX(ABS(b.total_assets), ABS(b.total_liabilities))
ORDER BY difference DESC;

-- 7
SELECT c.company_name, cf.year,
       cf.cash_from_operating + cf.cash_from_investing + cf.cash_from_financing AS calculated_net_cash,
       cf.net_cash_flow
FROM cashflow cf
JOIN companies c ON c.company_id = cf.company_id;

-- 8
SELECT c.company_name, MIN(sp.date) AS first_date, MAX(sp.date) AS last_date,
       COUNT(*) AS price_rows
FROM stock_prices sp
JOIN companies c ON c.company_id = sp.company_id
GROUP BY c.company_id, c.company_name
ORDER BY price_rows DESC;

-- 9
WITH latest AS (
    SELECT company_id, MAX(date) AS max_date
    FROM stock_prices GROUP BY company_id
)
SELECT c.company_name, sp.date, sp.close
FROM stock_prices sp
JOIN latest l ON l.company_id = sp.company_id AND l.max_date = sp.date
JOIN companies c ON c.company_id = sp.company_id
ORDER BY sp.close DESC;

-- 10
PRAGMA foreign_key_check;
