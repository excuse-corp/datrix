BEGIN;

CREATE SCHEMA IF NOT EXISTS reporting AUTHORIZATION dataman_admin;

-- One row per project. Receipts are aggregated by the verified XMID = XH key.
CREATE OR REPLACE VIEW reporting.vw_research_project_funding AS
WITH funding_by_project AS (
  SELECT
    f."XMID",
    count(*)::bigint AS receipt_record_count,
    sum(f."DZJF") AS amount_received,
    sum(f."LXJF") AS amount_retained_at_school,
    sum(f."WBJF") AS amount_transferred_out,
    min(f."DZSJ") AS first_receipt_at,
    max(f."DZSJ") AS last_receipt_at,
    string_agg(
      DISTINCT NULLIF(btrim(f."DZLX"), ''),
      ', ' ORDER BY NULLIF(btrim(f."DZLX"), '')
    ) AS receipt_types
  FROM sync.kypt_xmjf_znws AS f
  GROUP BY f."XMID"
)
SELECT
  p."XH" AS project_id,
  p."XMBH" AS project_no,
  p."XMMC" AS project_name,
  p."XMLYJLX" AS project_source_and_type,
  CASE
    WHEN btrim(p."LXSJ") ~ '^\d{4}-\d{2}-\d{2}$'
     AND to_char(to_date(btrim(p."LXSJ"), 'YYYY-MM-DD'), 'YYYY-MM-DD') = btrim(p."LXSJ")
    THEN btrim(p."LXSJ")::date
    ELSE NULL::date
  END AS project_approved_at,
  p."JFQK" AS project_funding_status_amount,
  p."FZR" AS project_leader_employee_no,
  p."XM" AS project_leader_name,
  p."XMJB" AS project_level,
  p."KYXMRDJBZW" AS research_project_recognition_level,
  p."KYRDJB" AS research_recognition_level_code,
  p."LXJF" AS project_approved_funding_amount_text,
  p."XMJBBM" AS project_level_code,
  p."SFGKKYJG" AS affiliated_research_institution_flag,
  p."GKKYJGMC" AS affiliated_research_institution_name,
  COALESCE(f.receipt_record_count, 0)::bigint AS receipt_record_count,
  f.amount_received,
  f.amount_retained_at_school,
  f.amount_transferred_out,
  f.first_receipt_at,
  f.last_receipt_at,
  f.receipt_types
FROM sync.kyxt_kyxm_znws AS p
LEFT JOIN funding_by_project AS f
  ON f."XMID" = p."XH";

COMMENT ON VIEW reporting.vw_research_project_funding IS
  '科研项目与经费场景；一行一个科研项目，经费到账按 XMID = XH 汇总。';
COMMENT ON COLUMN reporting.vw_research_project_funding.project_id IS '科研项目表内部ID（XH），与经费到账表XMID关联。';
COMMENT ON COLUMN reporting.vw_research_project_funding.project_no IS '项目业务编号。';
COMMENT ON COLUMN reporting.vw_research_project_funding.project_name IS '项目名称。';
COMMENT ON COLUMN reporting.vw_research_project_funding.project_source_and_type IS '项目来源及类型。';
COMMENT ON COLUMN reporting.vw_research_project_funding.project_approved_at IS '立项时间；无法解析为有效日期的原值显示为空。';
COMMENT ON COLUMN reporting.vw_research_project_funding.project_funding_status_amount IS '项目表经费情况字段，按源系统原口径保留。';
COMMENT ON COLUMN reporting.vw_research_project_funding.project_leader_employee_no IS '项目负责人职工号。';
COMMENT ON COLUMN reporting.vw_research_project_funding.project_leader_name IS '项目负责人姓名。';
COMMENT ON COLUMN reporting.vw_research_project_funding.project_level IS '项目级别。';
COMMENT ON COLUMN reporting.vw_research_project_funding.research_project_recognition_level IS '科研项目认定级别中文。';
COMMENT ON COLUMN reporting.vw_research_project_funding.research_recognition_level_code IS '科研认定级别编码。';
COMMENT ON COLUMN reporting.vw_research_project_funding.project_approved_funding_amount_text IS '项目表立项经费原值，源字段为文本；不与到账表留校经费混用。';
COMMENT ON COLUMN reporting.vw_research_project_funding.project_level_code IS '项目级别编码。';
COMMENT ON COLUMN reporting.vw_research_project_funding.affiliated_research_institution_flag IS '是否挂靠科研机构。';
COMMENT ON COLUMN reporting.vw_research_project_funding.affiliated_research_institution_name IS '挂靠科研机构名称。';
COMMENT ON COLUMN reporting.vw_research_project_funding.receipt_record_count IS '该项目到账记录数；没有到账记录时为0。';
COMMENT ON COLUMN reporting.vw_research_project_funding.amount_received IS '该项目累计到账金额（到账表DZJF求和）；无到账记录时为空。';
COMMENT ON COLUMN reporting.vw_research_project_funding.amount_retained_at_school IS '该项目累计留校经费（到账表LXJF求和）；无到账记录时为空。';
COMMENT ON COLUMN reporting.vw_research_project_funding.amount_transferred_out IS '该项目累计外拨经费（到账表WBJF求和）；无到账记录时为空。';
COMMENT ON COLUMN reporting.vw_research_project_funding.first_receipt_at IS '最早到账时间。';
COMMENT ON COLUMN reporting.vw_research_project_funding.last_receipt_at IS '最近到账时间。';
COMMENT ON COLUMN reporting.vw_research_project_funding.receipt_types IS '到账类型去重汇总，以逗号分隔。';

CREATE OR REPLACE VIEW reporting.vw_research_institutions AS
SELECT
  "ID" AS institution_id,
  "JGMC" AS institution_name,
  "FZR" AS director_name,
  "PTZXFZR" AS platform_executive_director_name,
  "GKDW" AS affiliated_unit,
  "JGJBBM" AS institution_level_code,
  "JGJBMC" AS institution_level_name,
  "JGLB" AS institution_category,
  "PTJJ" AS platform_introduction,
  "CLRQ" AS established_at,
  "ZYXKLY" AS main_discipline_areas,
  "ZYJSLY" AS main_technology_areas,
  "JGBH" AS institution_no
FROM sync.kypt_kyjg_znws;

COMMENT ON VIEW reporting.vw_research_institutions IS '科研机构场景；每行一条科研机构记录。';
COMMENT ON COLUMN reporting.vw_research_institutions.institution_id IS '机构记录ID。';
COMMENT ON COLUMN reporting.vw_research_institutions.institution_name IS '机构名称。';
COMMENT ON COLUMN reporting.vw_research_institutions.director_name IS '负责人。';
COMMENT ON COLUMN reporting.vw_research_institutions.platform_executive_director_name IS '平台执行负责人。';
COMMENT ON COLUMN reporting.vw_research_institutions.affiliated_unit IS '挂靠单位。';
COMMENT ON COLUMN reporting.vw_research_institutions.institution_level_code IS '机构级别编码。';
COMMENT ON COLUMN reporting.vw_research_institutions.institution_level_name IS '机构级别名称。';
COMMENT ON COLUMN reporting.vw_research_institutions.institution_category IS '机构类别。';
COMMENT ON COLUMN reporting.vw_research_institutions.platform_introduction IS '平台简介。';
COMMENT ON COLUMN reporting.vw_research_institutions.established_at IS '成立日期。';
COMMENT ON COLUMN reporting.vw_research_institutions.main_discipline_areas IS '主要学科领域。';
COMMENT ON COLUMN reporting.vw_research_institutions.main_technology_areas IS '主要技术领域。';
COMMENT ON COLUMN reporting.vw_research_institutions.institution_no IS '机构编号。';

CREATE OR REPLACE VIEW reporting.vw_research_papers AS
SELECT
  "ZJ" AS participation_record_id,
  "LWID" AS paper_id,
  "MC" AS paper_title,
  "ZGH" AS employee_no,
  "XM" AS person_name,
  CASE
    WHEN btrim("FBCBSJ") ~ '^\d{4}-\d{2}-\d{2}$'
     AND to_char(to_date(btrim("FBCBSJ"), 'YYYY-MM-DD'), 'YYYY-MM-DD') = btrim("FBCBSJ")
    THEN btrim("FBCBSJ")::date
    ELSE NULL::date
  END AS published_at,
  "FBKW" AS journal_name,
  "SLB" AS indexing_category,
  "BRPX" AS author_order,
  "LWJBM" AS paper_level_code,
  "LWJB" AS paper_level,
  "SFGKKYJG" AS affiliated_research_institution_flag,
  "GKKYJGMC" AS affiliated_research_institution_name
FROM sync.kyxt_kylw_znws;

COMMENT ON VIEW reporting.vw_research_papers IS '科研论文场景；每行一条论文作者参与记录，论文总数按paper_id去重。';
COMMENT ON COLUMN reporting.vw_research_papers.participation_record_id IS '论文作者参与记录ID。';
COMMENT ON COLUMN reporting.vw_research_papers.paper_id IS '论文ID；同一论文可对应多位作者。';
COMMENT ON COLUMN reporting.vw_research_papers.paper_title IS '论文名称。';
COMMENT ON COLUMN reporting.vw_research_papers.employee_no IS '职工号。';
COMMENT ON COLUMN reporting.vw_research_papers.person_name IS '作者姓名。';
COMMENT ON COLUMN reporting.vw_research_papers.published_at IS '发表出版时间；无法解析为有效日期的原值显示为空。';
COMMENT ON COLUMN reporting.vw_research_papers.journal_name IS '发表刊物。';
COMMENT ON COLUMN reporting.vw_research_papers.indexing_category IS '收录类别。';
COMMENT ON COLUMN reporting.vw_research_papers.author_order IS '本人排序。';
COMMENT ON COLUMN reporting.vw_research_papers.paper_level_code IS '论文级别码。';
COMMENT ON COLUMN reporting.vw_research_papers.paper_level IS '论文级别。';
COMMENT ON COLUMN reporting.vw_research_papers.affiliated_research_institution_flag IS '是否挂靠科研机构。';
COMMENT ON COLUMN reporting.vw_research_papers.affiliated_research_institution_name IS '挂靠科研机构名称。';

CREATE OR REPLACE VIEW reporting.vw_research_awards AS
SELECT
  "XH" AS award_record_id,
  "CGMC" AS award_winning_work_title,
  "MC" AS award_name,
  "HJJBBM" AS award_level_code,
  "HJJBMC" AS award_level,
  "HJDJ" AS award_grade,
  "ZZXM" AS recipient_name,
  "ZGH" AS employee_no,
  CASE
    WHEN btrim("HJRQ") ~ '^\d{4}/\d{2}/\d{2}'
     AND to_char(to_date(left(btrim("HJRQ"), 10), 'YYYY/MM/DD'), 'YYYY/MM/DD') = left(btrim("HJRQ"), 10)
    THEN to_date(left(btrim("HJRQ"), 10), 'YYYY/MM/DD')
    ELSE NULL::date
  END AS awarded_at,
  "BRPX" AS recipient_order,
  "SSDW" AS affiliated_unit,
  "SSKYJG" AS affiliated_research_institution_name,
  "SFGKKYJG" AS affiliated_research_institution_flag
FROM sync.kyxt_kycg_znws;

COMMENT ON VIEW reporting.vw_research_awards IS '科研获奖场景；每行一条获奖人员记录，award_name表示奖项名称而非获奖作品类型。';
COMMENT ON COLUMN reporting.vw_research_awards.award_record_id IS '获奖记录ID。';
COMMENT ON COLUMN reporting.vw_research_awards.award_winning_work_title IS '获奖成果名称。';
COMMENT ON COLUMN reporting.vw_research_awards.award_name IS '奖项或评奖项目名称。';
COMMENT ON COLUMN reporting.vw_research_awards.award_level_code IS '获奖级别编码。';
COMMENT ON COLUMN reporting.vw_research_awards.award_level IS '获奖级别名称。';
COMMENT ON COLUMN reporting.vw_research_awards.award_grade IS '获奖等级。';
COMMENT ON COLUMN reporting.vw_research_awards.recipient_name IS '获奖人姓名。';
COMMENT ON COLUMN reporting.vw_research_awards.employee_no IS '职工号。';
COMMENT ON COLUMN reporting.vw_research_awards.awarded_at IS '获奖日期；无法解析为有效日期的原值显示为空。';
COMMENT ON COLUMN reporting.vw_research_awards.recipient_order IS '本人排序。';
COMMENT ON COLUMN reporting.vw_research_awards.affiliated_unit IS '所属单位。';
COMMENT ON COLUMN reporting.vw_research_awards.affiliated_research_institution_name IS '所属科研机构。';
COMMENT ON COLUMN reporting.vw_research_awards.affiliated_research_institution_flag IS '是否挂靠科研机构。';

CREATE OR REPLACE VIEW reporting.vw_research_books AS
SELECT
  "XH" AS participation_record_id,
  "ZZID" AS book_id,
  "ZZMC" AS book_title,
  "JZGH" AS employee_no,
  "CBDW" AS publisher_name,
  "XXSM" AS school_signature,
  "SSDW" AS affiliated_unit,
  "SFWZYCBS" AS important_publisher_flag,
  CASE
    WHEN btrim("CBSJ") ~ '^\d{4}-\d{2}-\d{2}$'
     AND to_char(to_date(btrim("CBSJ"), 'YYYY-MM-DD'), 'YYYY-MM-DD') = btrim("CBSJ")
    THEN btrim("CBSJ")::date
    ELSE NULL::date
  END AS published_at,
  "ZZS" AS word_count,
  "ZZPX" AS author_order,
  "ZZLB" AS book_category,
  "ZZLBM" AS book_category_code,
  "LB" AS category_code
FROM sync.kyxt_kyzz_znws;

COMMENT ON VIEW reporting.vw_research_books IS '科研著作场景；每行一条著作作者参与记录，著作总数按book_id去重。';
COMMENT ON COLUMN reporting.vw_research_books.participation_record_id IS '著作作者参与记录序号。';
COMMENT ON COLUMN reporting.vw_research_books.book_id IS '著作ID；同一本著作可对应多位作者。';
COMMENT ON COLUMN reporting.vw_research_books.book_title IS '著作名称。';
COMMENT ON COLUMN reporting.vw_research_books.employee_no IS '教职工号。';
COMMENT ON COLUMN reporting.vw_research_books.publisher_name IS '出版单位。';
COMMENT ON COLUMN reporting.vw_research_books.school_signature IS '学校署名。';
COMMENT ON COLUMN reporting.vw_research_books.affiliated_unit IS '所属单位。';
COMMENT ON COLUMN reporting.vw_research_books.important_publisher_flag IS '是否为重要出版社；保留源值，不把空值解释为否。';
COMMENT ON COLUMN reporting.vw_research_books.published_at IS '出版时间；无法解析为有效日期的原值显示为空。';
COMMENT ON COLUMN reporting.vw_research_books.word_count IS '总字数，按源系统数值保留。';
COMMENT ON COLUMN reporting.vw_research_books.author_order IS '作者排序。';
COMMENT ON COLUMN reporting.vw_research_books.book_category IS '著作类别。';
COMMENT ON COLUMN reporting.vw_research_books.book_category_code IS '著作类别码。';
COMMENT ON COLUMN reporting.vw_research_books.category_code IS '类别编码。';

CREATE OR REPLACE VIEW reporting.vw_research_social_service AS
SELECT
  "ID" AS service_record_id,
  "TJSJ" AS submitted_at,
  "SMSX" AS author_order,
  "CGMC" AS achievement_title,
  "SSDW" AS affiliated_unit,
  "TJDW" AS submitting_unit,
  "SFBCN" AS adopted_flag,
  "CNDX" AS adopted_by,
  "XKML" AS discipline_category,
  "YJXK" AS first_level_discipline,
  "XMLY" AS project_source,
  "ZZS" AS word_count_text,
  "XXSM" AS school_signature,
  "DYZZXM" AS first_author_name,
  "RDJB" AS recognition_level,
  "XYRDJB" AS college_recognition_level,
  "CGLX" AS achievement_category,
  "JZGH" AS employee_no
FROM sync.kyxt_zkyshfw_znws;

COMMENT ON VIEW reporting.vw_research_social_service IS '科研社会服务成果场景；每行一条社会服务成果提交记录。';
COMMENT ON COLUMN reporting.vw_research_social_service.service_record_id IS '社会服务成果记录ID。';
COMMENT ON COLUMN reporting.vw_research_social_service.submitted_at IS '提交时间。';
COMMENT ON COLUMN reporting.vw_research_social_service.author_order IS '署名顺序。';
COMMENT ON COLUMN reporting.vw_research_social_service.achievement_title IS '成果名称。';
COMMENT ON COLUMN reporting.vw_research_social_service.affiliated_unit IS '所属单位。';
COMMENT ON COLUMN reporting.vw_research_social_service.submitting_unit IS '提交单位。';
COMMENT ON COLUMN reporting.vw_research_social_service.adopted_flag IS '是否被采纳；空值表示源数据未提供。';
COMMENT ON COLUMN reporting.vw_research_social_service.adopted_by IS '采纳对象。';
COMMENT ON COLUMN reporting.vw_research_social_service.discipline_category IS '学科门类。';
COMMENT ON COLUMN reporting.vw_research_social_service.first_level_discipline IS '一级学科。';
COMMENT ON COLUMN reporting.vw_research_social_service.project_source IS '项目来源。';
COMMENT ON COLUMN reporting.vw_research_social_service.word_count_text IS '总字数原值；源字段为文本。';
COMMENT ON COLUMN reporting.vw_research_social_service.school_signature IS '学校署名。';
COMMENT ON COLUMN reporting.vw_research_social_service.first_author_name IS '第一作者姓名。';
COMMENT ON COLUMN reporting.vw_research_social_service.recognition_level IS '认定级别。';
COMMENT ON COLUMN reporting.vw_research_social_service.college_recognition_level IS '学院认定级别。';
COMMENT ON COLUMN reporting.vw_research_social_service.achievement_category IS '成果类型；当前同步数据该字段为空。';
COMMENT ON COLUMN reporting.vw_research_social_service.employee_no IS '教职工号；空值表示源记录未提供。';

GRANT USAGE ON SCHEMA reporting TO dataman_reader;
GRANT SELECT ON TABLE
  reporting.vw_research_project_funding,
  reporting.vw_research_institutions,
  reporting.vw_research_papers,
  reporting.vw_research_awards,
  reporting.vw_research_books,
  reporting.vw_research_social_service
TO dataman_reader;

COMMIT;
