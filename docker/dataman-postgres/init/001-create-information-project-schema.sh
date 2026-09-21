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

CREATE TABLE public.vm_list (
  id integer NOT NULL,
  name varchar(200) NOT NULL,
  power_state varchar(20) NOT NULL,
  health_state varchar(10) NOT NULL,
  provisioned varchar(20) NOT NULL,
  used_space varchar(20) NOT NULL,
  host_cpu varchar(20) NOT NULL,
  host_memory varchar(20) NOT NULL,
  imported_at timestamp without time zone,
  CONSTRAINT pk_vm_list PRIMARY KEY (id)
);

COMMENT ON TABLE sync.pcm_project_info IS
  '第三方平台同步的远程 dbo.vw_xmk_project_contract_report 空表，不由 DataMan 写入。';

COMMENT ON TABLE public.vm_list IS
  '虚拟机清单(来源:虚拟机清单.xlsx)';
COMMENT ON COLUMN public.vm_list.name IS '名称';
COMMENT ON COLUMN public.vm_list.power_state IS '状况(电源)';
COMMENT ON COLUMN public.vm_list.health_state IS '状态(健康)';
COMMENT ON COLUMN public.vm_list.provisioned IS '置备的空间';
COMMENT ON COLUMN public.vm_list.used_space IS '已用空间';
COMMENT ON COLUMN public.vm_list.host_cpu IS '主机CPU';
COMMENT ON COLUMN public.vm_list.host_memory IS '主机内存';
COMMENT ON COLUMN public.vm_list.imported_at IS '导入时间';

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

CREATE VIEW reporting.vw_vm_list AS
SELECT
  source.id,
  source.name,
  source.power_state,
  source.health_state,
  source.provisioned,
  source.used_space,
  source.host_cpu,
  source.host_memory,
  source.imported_at
FROM public.vm_list AS source;

COMMENT ON VIEW reporting.vw_information_project_contract_report IS
  'DataMan 信息化项目场景唯一允许读取的视图。';

COMMENT ON VIEW reporting.vw_vm_list IS
  'DataMan 虚拟机清单场景唯一允许读取的视图。';
COMMENT ON COLUMN reporting.vw_vm_list.id IS '虚拟机清单ID';
COMMENT ON COLUMN reporting.vw_vm_list.name IS '名称';
COMMENT ON COLUMN reporting.vw_vm_list.power_state IS '状况(电源)';
COMMENT ON COLUMN reporting.vw_vm_list.health_state IS '状态(健康)';
COMMENT ON COLUMN reporting.vw_vm_list.provisioned IS '置备的空间';
COMMENT ON COLUMN reporting.vw_vm_list.used_space IS '已用空间';
COMMENT ON COLUMN reporting.vw_vm_list.host_cpu IS '主机CPU';
COMMENT ON COLUMN reporting.vw_vm_list.host_memory IS '主机内存';
COMMENT ON COLUMN reporting.vw_vm_list.imported_at IS '导入时间';

GRANT USAGE ON SCHEMA sync TO dataman_sync;
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLE
  sync.pcm_project_info TO dataman_sync;
GRANT USAGE ON SCHEMA public TO dataman_sync;
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLE
  public.vm_list TO dataman_sync;

GRANT USAGE ON SCHEMA sync TO dataman_reader;
GRANT SELECT ON TABLE sync.pcm_project_info TO dataman_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA sync
  GRANT SELECT ON TABLES TO dataman_reader;

GRANT USAGE ON SCHEMA reporting TO dataman_reader;
GRANT SELECT ON TABLE reporting.vw_information_project_contract_report TO dataman_reader;
GRANT SELECT ON TABLE reporting.vw_vm_list TO dataman_reader;
SQL
