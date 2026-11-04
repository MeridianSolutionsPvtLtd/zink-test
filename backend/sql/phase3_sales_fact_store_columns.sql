/*
  StockPulse Phase 3 — Customer Master columns on sales_fact

  Why: Sales Master site_id is a display name ("PT-South City (P033)"), while UI filters
  send Customer Master codes (1218, CW021, (SGN-01)). Joining store_master on site_id
  matched only ~4% of FY27 rows, so store / zone / grade / city filters returned wrong
  totals. The loader now copies the prepared FY parquet (already enriched with Customer
  Master — the exact data DuckDB serves) into these columns, and filters read them
  directly with no join.

  Run once (idempotent). Adding NULL columns is a metadata-only change.
  Then reload sales:  python scripts/load_filter_sources_to_azure_sql.py --only sales
*/

DECLARE @cols TABLE (name SYSNAME, ddl NVARCHAR(200));
INSERT INTO @cols (name, ddl) VALUES
    ('site_name',        'NVARCHAR(256) NULL'),
    ('site_code',        'NVARCHAR(64)  NULL'),
    ('site_manual_code', 'NVARCHAR(64)  NULL'),
    ('site_short_code',  'NVARCHAR(64)  NULL'),
    ('site_id_name',     'NVARCHAR(400) NULL'),
    ('site_key_paren',   'NVARCHAR(64)  NULL'),
    ('site_key_dash',    'NVARCHAR(64)  NULL'),
    ('customer_name',    'NVARCHAR(256) NULL'),
    ('channel_master',   'NVARCHAR(64)  NULL'),
    ('store_type',       'NVARCHAR(64)  NULL'),
    ('store_grade',      'NVARCHAR(32)  NULL'),
    ('city',             'NVARCHAR(128) NULL'),
    ('state_name',       'NVARCHAR(128) NULL'),
    ('cluster_name',     'NVARCHAR(128) NULL'),
    ('mrp',              'FLOAT NULL'),
    ('mrp_gross_val',    'FLOAT NULL'),
    ('discount_amount',  'FLOAT NULL'),
    ('discount_pct',     'FLOAT NULL'),
    ('tax_amount',       'FLOAT NULL'),
    ('size_label',       'NVARCHAR(64)  NULL'),
    ('store_category',   'NVARCHAR(32)  NULL'),
    ('store_sqft',       'FLOAT NULL'),
    ('mdq',              'FLOAT NULL');

DECLARE @name SYSNAME, @ddl NVARCHAR(200), @sql NVARCHAR(400);
DECLARE c CURSOR LOCAL FAST_FORWARD FOR SELECT name, ddl FROM @cols;
OPEN c;
FETCH NEXT FROM c INTO @name, @ddl;
WHILE @@FETCH_STATUS = 0
BEGIN
    IF COL_LENGTH('stockpulse.sales_fact', @name) IS NULL
    BEGIN
        SET @sql = N'ALTER TABLE stockpulse.sales_fact ADD ' + QUOTENAME(@name) + N' ' + @ddl;
        EXEC sp_executesql @sql;
    END
    FETCH NEXT FROM c INTO @name, @ddl;
END
CLOSE c;
DEALLOCATE c;
GO

SELECT name, TYPE_NAME(user_type_id) AS type_name, max_length
FROM sys.columns
WHERE object_id = OBJECT_ID('stockpulse.sales_fact')
ORDER BY column_id;
GO
