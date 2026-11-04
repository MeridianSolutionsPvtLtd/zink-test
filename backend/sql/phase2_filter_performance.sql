/*
  StockPulse Phase 2 — indexes for filter queries (Azure SQL)

  Run once in SSMS / Azure Data Studio after phase1_azure_sql_schema.sql, ideally right
  after a full sales load (the columnstore conversion rewrites the table once).
  Idempotent: every step checks what already exists.

  Why:
    * Filter pages aggregate millions of sales lines (SUM / GROUP BY). A clustered
      COLUMNSTORE index reads only the columns a query touches, runs in batch mode and
      skips whole row groups outside the sale_date range — typically 10x+ faster than
      the rowstore clustered index for these queries.
    * Store / SKU / department filters get B-tree indexes so narrow filters seek
      instead of scanning.

  Requirement: columnstore needs Azure SQL Database Standard S3+ / any Premium / any
  vCore tier. On Basic / S0-S2 skip step 1 (the B-tree indexes in step 2 still help).
*/

/* 1. sales_fact: rowstore clustered (sale_date, site_id) → clustered columnstore.
      DROP_EXISTING keeps the index name the loader expects; MAXDOP 1 keeps rows in
      sale_date order so row groups get tight date ranges (segment elimination). */
IF EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID('stockpulse.sales_fact')
      AND name = 'IX_sales_fact_date_site'
      AND type_desc = 'CLUSTERED'
)
BEGIN
    CREATE CLUSTERED COLUMNSTORE INDEX IX_sales_fact_date_site
        ON stockpulse.sales_fact
        WITH (DROP_EXISTING = ON, MAXDOP = 1);
END
GO

/* 2. B-tree indexes for selective filters (store, SKU, product hierarchy). */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('stockpulse.sales_fact') AND name = 'IX_sales_fact_site_date')
    CREATE NONCLUSTERED INDEX IX_sales_fact_site_date
        ON stockpulse.sales_fact (site_id, sale_date)
        INCLUDE (qty, net_sale_amount, gross_sale_amount);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('stockpulse.sales_fact') AND name = 'IX_sales_fact_sku')
    CREATE NONCLUSTERED INDEX IX_sales_fact_sku
        ON stockpulse.sales_fact (sku, sale_date)
        INCLUDE (qty, net_sale_amount, gross_sale_amount);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('stockpulse.sales_fact') AND name = 'IX_sales_fact_dept_section')
    CREATE NONCLUSTERED INDEX IX_sales_fact_dept_section
        ON stockpulse.sales_fact (department, section, sale_date)
        INCLUDE (qty, net_sale_amount, gross_sale_amount);
GO

/* 3. soh_fact: store filter on the latest snapshot. PK (as_of_date, site_id, barcode)
      already seeks the date; this covers barcode → department lookups via item_master. */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('stockpulse.soh_fact') AND name = 'IX_soh_fact_date_barcode')
    CREATE NONCLUSTERED INDEX IX_soh_fact_date_barcode
        ON stockpulse.soh_fact (as_of_date, barcode)
        INCLUDE (current_soh, site_id);
GO

/* 4. Fresh statistics so the optimizer sees the new data distribution. */
UPDATE STATISTICS stockpulse.sales_fact;
UPDATE STATISTICS stockpulse.store_master;
UPDATE STATISTICS stockpulse.soh_fact;
GO

/* Check: should show CLUSTERED COLUMNSTORE + the nonclustered indexes. */
SELECT i.name, i.type_desc
FROM sys.indexes i
WHERE i.object_id = OBJECT_ID('stockpulse.sales_fact')
ORDER BY i.index_id;
GO
