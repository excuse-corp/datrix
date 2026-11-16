# Neo4j 接入信息

## 当前状态

Neo4j 已通过 Docker 启动并完成连接验证。由于 Docker Hub 访问超时，本次使用 `docker.1ms.run` 拉取镜像后，在本地重新标记为标准镜像名运行。端口和数据卷配置已经固定。

## 部署配置

配置文件：[docker/neo4j/compose.yml](/root/datrix/docker/neo4j/compose.yml)

启动目录：`/root/datrix/docker/neo4j`

容器名称：`dataman-neo4j`

镜像：`neo4j:5.26-community`

Neo4j 容器端口和宿主机端口如下：

| 用途 | 容器端口 | 宿主机地址 |
| --- | ---: | --- |
| Bolt 驱动连接 | 7687 | `127.0.0.1:7670` |
| Web 管理界面 | 7474 | `http://127.0.0.1:7470` |

Neo4j 数据库名为 `neo4j`，用户名为 `neo4j`。

密码保存在被 Git 忽略的本地文件：

```text
/root/datrix/docker/neo4j/.env
```

该文件权限已设置为仅当前用户可读写，不要提交到 Git，也不要把密码写入 Markdown、日志或前端配置。接入应用时使用环境变量或部署密钥注入。

## 启动和检查

启动、查看状态和日志：

```bash
cd /root/datrix/docker/neo4j
docker compose up -d
docker compose ps
docker compose logs --tail=100 neo4j
```

检查 Web 管理端：

```bash
curl -I http://127.0.0.1:7470
```

应用使用 Bolt 地址：

```text
bolt://127.0.0.1:7670
```

停止服务：

```bash
cd /root/datrix/docker/neo4j
docker compose stop
```

删除容器但保留数据卷：

```bash
cd /root/datrix/docker/neo4j
docker compose down
```

删除容器和 Neo4j 数据卷会丢失图数据，只有确认需要重置环境时才执行：

```bash
cd /root/datrix/docker/neo4j
docker compose down -v
```

## Datrix 接入参数

本体发布图存储使用以下配置项。密码不要写入 `configs/*.toml`，通过本地 `.env`、部署密钥或进程环境变量注入：

```text
DATAMAN_NEO4J_ENABLED=true
DATAMAN_NEO4J_URI=bolt://127.0.0.1:7670
DATAMAN_NEO4J_USERNAME=neo4j
DATAMAN_NEO4J_PASSWORD=<从 docker/neo4j/.env 注入>
DATAMAN_NEO4J_DATABASE=neo4j
```

这些 `DATAMAN_NEO4J_*` 配置已经接入 AskData 本体的发布存储链路，但默认 `DATAMAN_NEO4J_ENABLED=0`，不会自动连接数据库。启用后，构建本体快照会把三层规范化对象同步到 Neo4j，管理员激活前会检查 Neo4j 快照是否为 `ready`。关系型元数据库仍保存草稿、审核记录和 JSON 兼容投影；本体运行时召回暂时仍使用 JSON 投影，尚未迁移到 Neo4j。

管理接口（均要求管理员权限）：

```text
GET  /api/v1/ask-data/ontology/neo4j/health
GET  /api/v1/ask-data/ontology/snapshots/{snapshot_id}/sync-status
POST /api/v1/ask-data/ontology/snapshots/{snapshot_id}/sync
```

首次健康检查或快照同步会幂等创建本体图的约束和索引。对象统一带 `snapshot_id`，已知对象类型会附带对应 Neo4j 标签，关系使用固定白名单类型；同步完成后会校验节点数和关系数。

## 本次安装结果

已完成：

- 检查 Docker 和 Docker Compose 可用；
- 确认宿主机端口 `7670` 未被占用；
- 创建 Neo4j Compose 配置、持久化数据卷和本地凭据文件；
- 配置 Bolt `7670` 和 Web `7470` 端口映射。
- 从 `docker.1ms.run` 拉取 Neo4j `5.26-community` 镜像并启动 `dataman-neo4j`；
- 通过 Web 管理端返回 HTTP 200，并使用容器内 Cypher Shell 执行 `RETURN 1` 验证数据库可用。

当前尚未完成的是把查数后的运行时召回切换为 Neo4j 读图；Neo4j 发布存储链路和管理状态接口已经接入。

## 参考文档

- [Neo4j Docker 部署](https://neo4j.com/docs/operations-manual/current/docker/introduction/)
- [Neo4j Docker Compose](https://neo4j.com/docs/operations-manual/current/docker/docker-compose-standalone/)
- [Neo4j Docker 数据卷](https://neo4j.com/docs/operations-manual/current/docker/mounting-volumes/)
