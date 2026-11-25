# 科研社会服务场景数据字典

- 查询视图：`reporting.vw_research_social_service`
- 来源表：`sync.kyxt_zkyshfw_znws`
- 数据粒度：每行一条社会服务成果提交记录。
- 记录标识：`service_record_id`（来源 `ID`）。

| 字段 | 类型 | 说明 |
|---|---|---|
| `service_record_id` | varchar(180) | 社会服务成果记录 ID。 |
| `submitted_at` | timestamp | 提交时间。 |
| `author_order` | varchar(60) | 署名顺序。 |
| `achievement_title` | varchar(1500) | 成果名称。 |
| `affiliated_unit` | varchar(1500) | 所属单位。 |
| `submitting_unit` | varchar(1500) | 提交单位。 |
| `adopted_flag` | varchar(60) | 是否被采纳；空值表示源数据未提供。 |
| `adopted_by` | varchar(1500) | 采纳对象。 |
| `discipline_category` | varchar(150) | 学科门类。 |
| `first_level_discipline` | varchar(300) | 一级学科。 |
| `project_source` | varchar(1500) | 项目来源。 |
| `word_count_text` | varchar(60) | 总字数原值，源字段为文本。 |
| `school_signature` | varchar(1500) | 学校署名。 |
| `first_author_name` | varchar(1500) | 第一作者姓名。 |
| `recognition_level` | varchar(600) | 认定级别。 |
| `college_recognition_level` | varchar(600) | 学院认定级别。 |
| `achievement_category` | varchar(600) | 成果类型；当前同步数据中为空。 |
| `employee_no` | varchar(180) | 教职工号；空值表示源记录未提供。 |

口径注意：当前 1,266 条记录中，“是否被采纳”有 690 条为空，不能将空值解释为未采纳；228 条记录没有教职工号。`project_source` 是项目来源描述，不是科研项目 ID，不能用于关联科研项目表。
