# 备份与恢复

备份应包含 PostgreSQL、MinIO 对象和部署配置。推荐至少每 15 分钟执行一次数据库增量或 WAL 归档，并定期演练恢复。

```bash
docker compose exec -T postgres pg_dump -U openkate -Fc openkate > openkate.dump
docker compose exec -T postgres pg_restore -U openkate -d openkate --clean --if-exists < openkate.dump
```

恢复前停止写入服务；恢复后先启动 migrations，再启动控制面和执行器。MinIO 中的证据对象必须按同一恢复点恢复，否则报告会出现缺失证据。
