/*
  Extra Phase 1 tables for dashboard filter sources.
  Run against database: zinklondon
*/

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'stockpulse')
    EXEC('CREATE SCHEMA stockpulse');
GO

/* Store grade list (from STORE_GRADE) */
IF OBJECT_ID('stockpulse.store_grade', 'U') IS NULL
BEGIN
    CREATE TABLE stockpulse.store_grade (
        site_id        NVARCHAR(64)  NOT NULL,
        store_grade    NVARCHAR(32)  NULL,
        zone           NVARCHAR(64)  NULL,
        site_name      NVARCHAR(256) NULL,
        store_category NVARCHAR(64)  NULL,
        updated_at     DATETIME2     NOT NULL CONSTRAINT DF_store_grade_upd DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_store_grade PRIMARY KEY (site_id)
    );
END
GO

/* Target LFS (Target vs Achieved filters) */
IF OBJECT_ID('stockpulse.target_lfs', 'U') IS NULL
BEGIN
    CREATE TABLE stockpulse.target_lfs (
        target_date    DATE           NOT NULL,
        site_id        NVARCHAR(64)   NOT NULL,
        store_name     NVARCHAR(256)  NULL,
        format_name    NVARCHAR(64)   NULL,
        target_amount  DECIMAL(18, 2) NOT NULL CONSTRAINT DF_target_amt DEFAULT 0,
        updated_at     DATETIME2      NOT NULL CONSTRAINT DF_target_upd DEFAULT SYSUTCDATETIME()
    );
    CREATE CLUSTERED INDEX IX_target_lfs_date_site
        ON stockpulse.target_lfs (target_date, site_id);
    CREATE NONCLUSTERED INDEX IX_target_lfs_format
        ON stockpulse.target_lfs (format_name);
END
GO

/* Widen item_master for filter dims used by Item Master / Analyzer */
IF COL_LENGTH('stockpulse.item_master', 'color') IS NULL
    ALTER TABLE stockpulse.item_master ADD color NVARCHAR(64) NULL;
IF COL_LENGTH('stockpulse.item_master', 'mrp') IS NULL
    ALTER TABLE stockpulse.item_master ADD mrp DECIMAL(18, 2) NULL;
IF COL_LENGTH('stockpulse.item_master', 'style_name') IS NULL
    ALTER TABLE stockpulse.item_master ADD style_name NVARCHAR(256) NULL;
GO

/* Widen store_master for Customer Master filter fields */
IF COL_LENGTH('stockpulse.store_master', 'store_sqft') IS NULL
    ALTER TABLE stockpulse.store_master ADD store_sqft DECIMAL(18, 2) NULL;
IF COL_LENGTH('stockpulse.store_master', 'store_category') IS NULL
    ALTER TABLE stockpulse.store_master ADD store_category NVARCHAR(32) NULL;
IF COL_LENGTH('stockpulse.store_master', 'site_manual_code') IS NULL
    ALTER TABLE stockpulse.store_master ADD site_manual_code NVARCHAR(64) NULL;
GO
