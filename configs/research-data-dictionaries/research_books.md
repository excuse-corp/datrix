# 著作场景数据字典

- 查询视图：`reporting.vw_research_books`
- 来源表：`sync.kyxt_kyzz_znws`
- 数据粒度：每行一条著作作者参与记录；一本著作可对应多位作者。
- 参与记录标识：`participation_record_id`；著作标识：`book_id`（来源 `ZZID`）。

| 字段 | 类型 | 说明 |
|---|---|---|
| `participation_record_id` | varchar(361) | 著作作者参与记录序号。 |
| `book_id` | varchar(180) | 著作 ID；同一本著作可对应多位作者。 |
| `book_title` | varchar(360) | 著作名称。 |
| `employee_no` | varchar(180) | 教职工号。 |
| `publisher_name` | varchar(300) | 出版单位。 |
| `school_signature` | varchar(60) | 学校署名。 |
| `affiliated_unit` | varchar(255) | 所属单位。 |
| `important_publisher_flag` | numeric | 是否为重要出版社；保留源值，空值不当作否。 |
| `published_at` | date | 出版时间；只转换有效的 `YYYY-MM-DD` 日期。 |
| `word_count` | numeric | 总字数，按源数值保留。 |
| `author_order` | varchar(60) | 作者排序。 |
| `book_category` | varchar(255) | 著作类别。 |
| `book_category_code` | varchar(30) | 著作类别码。 |
| `category_code` | char(2) | 类别编码。 |

口径注意：当前 3,189 条记录对应 2,947 个不同著作 ID。统计著作数按 `book_id` 去重；统计作者参与次数按记录行数。源字段没有作者姓名，人员查询使用教职工号；总字数为源系统口径，不在视图中换算单位。
