# 升级与回滚

1. 记录当前镜像标签和数据库备份。
2. 拉取目标版本并先执行 migrations。
3. 逐步重建服务，保留 PostgreSQL、MinIO 与 Temporal 数据卷。
4. 用 `/health`、系统健康页和一个只读场景验证。

```bash
docker compose pull
docker compose --profile core --profile executors up -d --build
```

若验证失败，回滚镜像标签并恢复升级前数据库备份。数据库迁移必须保持向后兼容；不得通过删除卷作为回滚手段。
