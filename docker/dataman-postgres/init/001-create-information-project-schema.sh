#!/usr/bin/env bash
set -euo pipefail

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 \
  --set=sync_password="$DATAMAN_SYNC_PASSWORD" \
  --set=reader_password="$DATAMAN_READER_PASSWORD" <<'SQL'
CREATE ROLE dataman_sync
  LOGIN
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOINHERIT
  NOREPLICATION
  PASSWORD :'sync_password';

CREATE ROLE dataman_reader
  LOGIN
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOINHERIT
  NOREPLICATION
  PASSWORD :'reader_password';

CREATE SCHEMA sync AUTHORIZATION dataman_admin;
CREATE SCHEMA reporting AUTHORIZATION dataman_admin;

CREATE TABLE sync.pcm_project_info (
  person_name varchar(255),
  employee_no varchar(64),
  department_name varchar(255),
  project_name varchar(500),
  project_type varchar(255),
  project_manager varchar(255),
  project_contact varchar(255),
  project_contract_amount numeric(18, 2),
  project_contract_name varchar(1000),
  contract_party_b varchar(1000),
  project_funding_source varchar(255),
  project_description text,
  funding_type varchar(255),
  project_ingested_at timestamp without time zone,
  contract_paid_amount numeric(18, 2),
  winning_bidder varchar(1000),
  project_budget numeric(18, 2),
  project_phase varchar(255),
  project_status varchar(255),
  application_category varchar(255),
  funding_type_year varchar(64),
  project_grid_manager varchar(255),
  project_start_date date,
  project_bid_awarded_at date,
  project_id integer
);

COMMENT ON TABLE sync.pcm_project_info IS
  '第三方平台同步的远程 dbo.vw_xmk_project_contract_report 空表，不由 DataMan 写入。';

CREATE VIEW reporting.vw_information_project_contract_report AS
SELECT
  source.person_name,
  source.employee_no,
  source.department_name,
  source.project_name,
  source.project_type,
  source.project_manager,
  source.project_contact,
  source.project_contract_amount,
  source.project_contract_name,
  source.contract_party_b,
  source.project_funding_source,
  source.project_description,
  source.funding_type,
  source.project_ingested_at,
  source.contract_paid_amount,
  source.winning_bidder,
  source.project_budget,
  source.project_phase,
  source.project_status,
  source.application_category,
  source.funding_type_year,
  source.project_grid_manager,
  source.project_start_date,
  source.project_bid_awarded_at,
  CASE
    WHEN source.department_name IN (
      '教育技术与资源制作中心',
      '网络与数据安全中心',
      '信息技术与应用开发中心',
      '网络技术与运维中心',
      '人工智能与智慧校园建设办公室'
    ) THEN '信息管理部'
    ELSE source.department_name
  END AS line_department,
  source.project_id
FROM sync.pcm_project_info AS source;

COMMENT ON VIEW reporting.vw_information_project_contract_report IS
  'DataMan 信息化项目场景唯一允许读取的视图。';

GRANT USAGE ON SCHEMA sync TO dataman_sync;
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLE
  sync.pcm_project_info TO dataman_sync;

GRANT USAGE ON SCHEMA sync TO dataman_reader;
GRANT SELECT ON TABLE sync.pcm_project_info TO dataman_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA sync
  GRANT SELECT ON TABLES TO dataman_reader;

GRANT USAGE ON SCHEMA reporting TO dataman_reader;
GRANT SELECT ON TABLE reporting.vw_information_project_contract_report TO dataman_reader;
SQL
