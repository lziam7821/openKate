# 安装

## 前置条件

- Docker Engine 与 Docker Compose v2
- 至少 8 GB 内存和 20 GB 可用磁盘
- 设置 `OPENKATE_JWT_SECRET`，不得使用默认开发值

## 启动

```bash
export OPENKATE_JWT_SECRET='replace-with-a-long-random-secret'
docker compose --profile core --profile executors up -d --build
```

等待 PostgreSQL 与 migrations 容器完成后，访问 `http://localhost:8000/health`。执行器能力可通过 `GET /api/v1/projects/{projectId}/executor-capabilities` 确认。

移动端需要额外设置 `OPENKATE_APPIUM_URL`；k6/ZAP 需要在 `executor-quality` 镜像中提供对应二进制，否则能力会显示为不可用。
