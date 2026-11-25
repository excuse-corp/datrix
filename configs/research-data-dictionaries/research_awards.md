# 获奖场景数据字典

- 查询视图：`reporting.vw_research_awards`
- 来源表：`sync.kyxt_kycg_znws`
- 数据粒度：每行一条获奖记录。
- 记录标识：`award_record_id`（来源 `XH`）。

| 字段 | 类型 | 说明 |
|---|---|---|
| `award_record_id` | varchar(180) | 获奖记录 ID。 |
| `award_winning_work_title` | varchar(600) | 获奖成果名称。 |
| `award_name` | varchar(600) | 奖项或评奖项目名称（来源 `MC`）。 |
| `award_level_code` | char(2) | 获奖级别编码。 |
| `award_level` | varchar(600) | 获奖级别名称。 |
| `award_grade` | varchar(600) | 获奖等级。 |
| `recipient_name` | varchar(300) | 获奖人姓名。 |
| `employee_no` | varchar(180) | 职工号。 |
| `awarded_at` | date | 获奖日期；从源文本日期中解析有效的 `YYYY/MM/DD` 部分。 |
| `recipient_order` | varchar(60) | 本人排序。 |
| `affiliated_unit` | varchar(1500) | 所属单位。 |
| `affiliated_research_institution_name` | text | 所属科研机构。 |
| `affiliated_research_institution_flag` | varchar(6) | 是否挂靠科研机构。 |

口径注意：`award_name` 是奖项名称，`award_winning_work_title` 是获奖作品名称；表中没有作品形态字段，不能据此自动分类为论文、著作、社会服务或项目。职工号为空的记录不能可靠归属到人员。统计记录数按 `award_record_id`，具体人员统计应明确是否排除职工号为空的记录。
