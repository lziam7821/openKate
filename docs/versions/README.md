# OpenKATE 版本规划索引

> 上位设计：`../openkate-rd-design.md`
> 架构图：`/Users/lziam/Desktop/code/diagram/openkate-full-architecture/openkate-full-architecture.svg`

## 规划基线

- 团队基线：2 名前端、4 名后端/Agent、1 名 QA、1 名产品或架构负责人。
- Sprint 长度：2 周；工期为相对估算，不作为固定日期承诺。
- 前端使用 TypeScript；后端、Agent、Workflow、Connector 和 Executor 使用 Python。
- 从首版采用 1 个 Gateway、9 个领域服务和独立 Executor Worker。
- 每个版本必须可独立部署、回滚和演示，验收通过后才能进入下一版本。

## 版本顺序

| 版本 | 主题 | 预计 Sprint | 文件 |
| --- | --- | --- | --- |
| `v0.1.0` | 工程与项目底座 | 3 | [v0.1.0.md](./v0.1.0.md) |
| `v0.2.0` | Validation Center | 2 | [v0.2.0.md](./v0.2.0.md) |
| `v0.3.0` | Execution Fabric MVP | 3 | [v0.3.0.md](./v0.3.0.md) |
| `v0.4.0` | Evidence & Report | 2 | [v0.4.0.md](./v0.4.0.md) |
| `v0.5.0` | AI Scenario Generation | 3 | [v0.5.0.md](./v0.5.0.md) |
| `v0.6.0` | Knowledge & Governance | 3 | [v0.6.0.md](./v0.6.0.md) |
| `v0.7.0` | CI/CD & Connectors | 2 | [v0.7.0.md](./v0.7.0.md) |
| `v0.8.0` | Extended Channels | 4 | [v0.8.0.md](./v0.8.0.md) |
| `v0.9.0` | Production Readiness | 3 | [v0.9.0.md](./v0.9.0.md) |
| `v1.0.0` | General Availability | 2 | [v1.0.0.md](./v1.0.0.md) |

## 统一完成标准

- 需求验收条件通过。
- API 和 Event Contract 已版本化。
- 单元、契约、集成和必要的端到端测试通过。
- 日志包含 `requestId`、`traceId`、`projectId`，敏感字段已脱敏。
- Migration 支持升级，并有明确回滚策略。
- 不存在跨服务数据库查询或内部代码 import。
- 用户、开发和运维文档随版本同步更新。

## 统一发布门禁

```text
lint -> type-check -> unit-test -> contract-test -> integration-test
     -> migration-test -> security-scan -> end-to-end-smoke
     -> image-build -> staging-deploy -> acceptance-test
```

## 契约约束

- 后端 Pydantic Model 是 API Schema 唯一事实源。
- OpenAPI 自动生成 Web 使用的 TypeScript API Client。
- Event Contract 使用版本化 JSON Schema。
- Consumer 至少兼容当前版本和前一个版本。
- 破坏性修改必须建立新版本和迁移窗口。

