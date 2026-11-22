# 科研数据场景字典

数据来源为 `dataman_data` 数据库的 `sync` 同步表；AskData 查询绑定 `reporting` 下的只读视图。

| 场景 | 查询视图 | 字典 |
|---|---|---|
| 项目与经费 | `reporting.vw_research_project_funding` | [项目与经费](research_project_funding.md) |
| 科研机构 | `reporting.vw_research_institutions` | [科研机构](research_institutions.md) |
| 论文 | `reporting.vw_research_papers` | [论文](research_papers.md) |
| 获奖 | `reporting.vw_research_awards` | [获奖](research_awards.md) |
| 著作 | `reporting.vw_research_books` | [著作](research_books.md) |
| 社会服务 | `reporting.vw_research_social_service` | [社会服务](research_social_service.md) |

查询视图 DDL 位于 `docker/dataman-postgres/migrations/003-create-research-reporting-views.sql`。此迁移要求七张科研同步表已经存在。
