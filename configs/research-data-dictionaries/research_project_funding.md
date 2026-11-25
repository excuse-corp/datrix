# 项目与经费场景数据字典

- 查询视图：`reporting.vw_research_project_funding`
- 来源表：`sync.kyxt_kyxm_znws`（科研项目）、`sync.kypt_xmjf_znws`（项目经费到账）
- 数据粒度：每行一个科研项目。到账表先按 `XMID` 汇总，再通过 `XMID = XH` 关联项目。
- 关联覆盖：当前数据库中的 7,992 个不同经费 `XMID` 均匹配项目 `XH`；项目表共有 10,814 个项目。

| 字段 | 类型 | 说明 |
|---|---|---|
| `project_id` | varchar(180) | 项目表内部 ID；关联经费表 `XMID`。 |
| `project_no` | varchar(300) | 项目业务编号。 |
| `project_name` | varchar(300) | 项目名称。 |
| `project_source_and_type` | char(12) | 项目来源及类型。 |
| `project_approved_at` | date | 立项时间；只转换有效的 `YYYY-MM-DD` 日期。 |
| `project_funding_status_amount` | numeric | 项目表“经费情况”字段，保留源口径。 |
| `project_leader_employee_no` | varchar(300) | 项目负责人职工号。 |
| `project_leader_name` | varchar(300) | 项目负责人姓名。 |
| `project_level` | varchar(1200) | 项目级别。 |
| `research_project_recognition_level` | varchar(300) | 科研项目认定级别中文。 |
| `research_recognition_level_code` | char(2) | 科研认定级别编码。 |
| `project_approved_funding_amount_text` | varchar(60) | 项目表“立项经费”原值；源字段是文本。 |
| `project_level_code` | varchar(300) | 项目级别编码。 |
| `affiliated_research_institution_flag` | varchar(6) | 是否挂靠科研机构。 |
| `affiliated_research_institution_name` | varchar(4000) | 挂靠科研机构名称。 |
| `receipt_record_count` | bigint | 到账记录数；没有到账记录时为 0。 |
| `amount_received` | numeric | 到账表 `DZJF` 按项目汇总。没有到账记录时为空。 |
| `amount_retained_at_school` | numeric | 到账表 `LXJF` 按项目汇总，表示留校经费。 |
| `amount_transferred_out` | numeric | 到账表 `WBJF` 按项目汇总，表示外拨经费。 |
| `first_receipt_at` | timestamp | 最早到账时间。 |
| `last_receipt_at` | timestamp | 最近到账时间。 |
| `receipt_types` | text | 项目到账类型去重后逗号分隔。 |

口径注意：项目表 `LXJF` 是“立项经费”，到账表 `LXJF` 是“留校经费”，两个字段含义不同。不要混用。到账表一项目可能有多笔记录；此视图已先汇总，适合项目级统计。金额空值表示源数据未提供，不自动当作 0。此视图不提供单笔到账明细。
