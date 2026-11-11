BEGIN;

ALTER TABLE sync.pcm_project_info
  ADD COLUMN IF NOT EXISTS funding_type_code integer,
  ADD COLUMN IF NOT EXISTS funding_year varchar(32);

COMMENT ON COLUMN sync.pcm_project_info.funding_type_year IS
  '经费年份原始选项编码，来源于 uf_xmk.jflxnf；不是实际年份。';
COMMENT ON COLUMN sync.pcm_project_info.funding_type_code IS
  '经费类型原始编码，来源于 uf_xmk.jflx。';
COMMENT ON COLUMN sync.pcm_project_info.funding_year IS
  '经费年份显示值，来源于年份字典 selectname；用于年份筛选和统计。';

CREATE OR REPLACE VIEW reporting.vw_information_project_contract_report AS
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
  source.project_id,
  source.funding_type_code,
  source.funding_year
FROM sync.pcm_project_info AS source;

COMMENT ON COLUMN reporting.vw_information_project_contract_report.funding_type_code IS
  '经费类型原始编码；排查和核对使用，业务展示优先使用 funding_type。';
COMMENT ON COLUMN reporting.vw_information_project_contract_report.funding_year IS
  '经费年份显示值；年份筛选、分组和统计使用此字段。';

COMMIT;
