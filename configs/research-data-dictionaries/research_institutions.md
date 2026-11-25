# 科研机构场景数据字典

- 查询视图：`reporting.vw_research_institutions`
- 来源表：`sync.kypt_kyjg_znws`
- 数据粒度：每行一条科研机构记录。
- 记录标识：`institution_id`（来源 `ID`）。

| 字段 | 类型 | 说明 |
|---|---|---|
| `institution_id` | varchar(96) | 机构记录 ID。 |
| `institution_name` | text | 机构名称。 |
| `director_name` | text | 负责人。 |
| `platform_executive_director_name` | text | 平台执行负责人。 |
| `affiliated_unit` | varchar(96) | 挂靠单位。 |
| `institution_level_code` | varchar(96) | 机构级别编码。 |
| `institution_level_name` | varchar(12) | 机构级别名称。 |
| `institution_category` | text | 机构类别。 |
| `platform_introduction` | text | 平台简介。 |
| `established_at` | timestamp | 成立日期。 |
| `main_discipline_areas` | text | 主要学科领域。 |
| `main_technology_areas` | text | 主要技术领域。 |
| `institution_no` | varchar(96) | 机构编号。 |

口径注意：负责人和执行负责人是不同角色；挂靠单位不等同于机构类别。机构统计优先按机构记录 ID 或经业务确认的机构编号去重，不建议仅按机构名称去重。
