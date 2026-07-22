# 容量与配额

`OPENKATE_MAX_ACTIVE_RUNS_PER_PROJECT` 控制每个项目同时运行的数量，默认值为 20。超出上限会在资源租约分配前返回 `429`。

容量规划时分别监控：执行器 Worker CPU/内存、Temporal task queue、NATS lag、PostgreSQL 连接数、MinIO 证据存储和设备池占用。移动设备、账号和数据集均是独占资源，应按并发目标预留。
