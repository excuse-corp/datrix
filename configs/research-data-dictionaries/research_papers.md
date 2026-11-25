# 论文场景数据字典

- 查询视图：`reporting.vw_research_papers`
- 来源表：`sync.kyxt_kylw_znws`
- 数据粒度：每行一条论文作者参与记录；一篇论文的不同作者分别占行。
- 记录标识：`participation_record_id`；论文标识：`paper_id`（来源 `LWID`）。

| 字段 | 类型 | 说明 |
|---|---|---|
| `participation_record_id` | varchar(289) | 论文作者参与记录 ID。 |
| `paper_id` | varchar(96) | 论文 ID；多位作者可共享一个论文 ID。 |
| `paper_title` | varchar(4000) | 论文名称。 |
| `employee_no` | varchar(192) | 职工号。 |
| `person_name` | varchar(300) | 作者姓名。 |
| `published_at` | date | 发表出版时间；只转换有效的 `YYYY-MM-DD` 日期。 |
| `journal_name` | varchar(4000) | 发表刊物。 |
| `indexing_category` | varchar(4000) | 收录类别。 |
| `author_order` | numeric | 本人排序。 |
| `paper_level_code` | char(2) | 论文级别码。 |
| `paper_level` | varchar(4000) | 论文级别。 |
| `affiliated_research_institution_flag` | varchar(24) | 是否挂靠科研机构。 |
| `affiliated_research_institution_name` | varchar(4000) | 挂靠科研机构名称。 |

口径注意：当前有 22,906 条作者参与记录和 21,593 个不同论文 ID。统计论文数按 `paper_id` 去重；统计教师参与次数按记录行数。论文级别存在大量空值，空值不代表“其他”或“未分级”。
