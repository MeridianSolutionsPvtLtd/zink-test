/*
  StockPulse Phase 1 — Azure SQL schema
  Server: zinklondon-db.database.windows.net

  Run in SSMS / Azure Data Studio against your database once.
  Idempotent: IF NOT EXISTS guards.
*/

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'stockpulse')
    EXEC('CREATE SCHEMA stockpulse');
GO

/* ---------- Masters ---------- */

IF OBJECT_ID('stockpulse.store_master', 'U') IS NULL
BEGIN
    CREATE TABLE stockpulse.store_master (
        site_id        NVARCHAR(64)  NOT NULL,
        site_name      NVARCHAR(256) NULL,
        sales_channel  NVARCHAR(64)  NULL,
        zone           NVARCHAR(64)  NULL,
        city           NVARCHAR(128) NULL,
        state_name     NVARCHAR(128) NULL,
        store_grade    NVARCHAR(32)  NULL,
        cluster_name   NVARCHAR(128) NULL,
        customer_name  NVARCHAR(256) NULL,
        updated_at     DATETIME2     NOT NULL CONSTRAINT DF_store_master_upd DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_store_master PRIMARY KEY (site_id)
    );
END
GO

IF OBJECT_ID('stockpulse.item_master', 'U') IS NULL
BEGIN
    CREATE TABLE stockpulse.item_master (
        barcode        NVARCHAR(64)  NOT NULL,
        sku            NVARCHAR(64)  NULL,
        department     NVARCHAR(128) NULL,
        section        NVARCHAR(128) NULL,
        season         NVARCHAR(64)  NULL,
        size_label     NVARCHAR(64)  NULL,
        updated_at     DATETIME2     NOT NULL CONSTRAINT DF_item_master_upd DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_item_master PRIMARY KEY (barcode)
    );
END
GO

/* ---------- Facts ---------- */

IF OBJECT_ID('stockpulse.sales_fact', 'U') IS NULL
BEGIN
    CREATE TABLE stockpulse.sales_fact (
        sale_date        DATE           NOT NULL,
        site_id          NVARCHAR(64)   NOT NULL,
        barcode          NVARCHAR(64)   NOT NULL,
        sku              NVARCHAR(64)   NULL,
        sales_channel    NVARCHAR(64)   NULL,
        zone             NVARCHAR(64)   NULL,
        department       NVARCHAR(128)  NULL,
        section          NVARCHAR(128)  NULL,
        season           NVARCHAR(64)   NULL,
        qty              DECIMAL(18, 4) NOT NULL CONSTRAINT DF_sales_qty DEFAULT 0,
        net_sale_amount  DECIMAL(18, 2) NOT NULL CONSTRAINT DF_sales_net DEFAULT 0,
        gross_sale_amount DECIMAL(18, 2) NULL,
        financial_year   NVARCHAR(16)   NULL,
        updated_at       DATETIME2      NOT NULL CONSTRAINT DF_sales_upd DEFAULT SYSUTCDATETIME()
    );
    CREATE CLUSTERED INDEX IX_sales_fact_date_site
        ON stockpulse.sales_fact (sale_date, site_id);
    CREATE NONCLUSTERED INDEX IX_sales_fact_channel_dept
        ON stockpulse.sales_fact (sales_channel, department)
        INCLUDE (net_sale_amount, qty);
    CREATE NONCLUSTERED INDEX IX_sales_fact_barcode
        ON stockpulse.sales_fact (barcode);
END
GO

IF OBJECT_ID('stockpulse.soh_fact', 'U') IS NULL
BEGIN
    CREATE TABLE stockpulse.soh_fact (
        as_of_date       DATE           NOT NULL,
        site_id          NVARCHAR(64)   NOT NULL,
        barcode          NVARCHAR(64)   NOT NULL,
        sales_channel    NVARCHAR(64)   NULL,
        department       NVARCHAR(128)  NULL,
        current_soh      DECIMAL(18, 4) NOT NULL CONSTRAINT DF_soh_qty DEFAULT 0,
        available_qty    DECIMAL(18, 4) NULL,
        dispatch_qty     DECIMAL(18, 4) NULL,
        updated_at       DATETIME2      NOT NULL CONSTRAINT DF_soh_upd DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_soh_fact PRIMARY KEY (as_of_date, site_id, barcode)
    );
    CREATE NONCLUSTERED INDEX IX_soh_fact_channel_dept
        ON stockpulse.soh_fact (sales_channel, department)
        INCLUDE (current_soh);
END
GO

/* Load watermark / ops */
IF OBJECT_ID('stockpulse.etl_watermark', 'U') IS NULL
BEGIN
    CREATE TABLE stockpulse.etl_watermark (
        pipeline_name    NVARCHAR(128) NOT NULL,
        last_success_at  DATETIME2     NULL,
        last_row_count   BIGINT        NULL,
        last_status      NVARCHAR(32)  NULL,
        last_message     NVARCHAR(1000) NULL,
        CONSTRAINT PK_etl_watermark PRIMARY KEY (pipeline_name)
    );
END
GO
