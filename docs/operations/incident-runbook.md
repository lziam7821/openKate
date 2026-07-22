# 事故处置

## Worker 不可用

检查系统健康与 Executor Capabilities。能力为 `unavailable` 时，不应创建对应通道的计划；恢复 Worker 后重新探测能力。

## 运行积压或卡住

检查 workflow-service、Temporal 与 execution-service。取消卡住运行会释放账号、数据集和设备租约；重试前确认外部副作用步骤具有幂等性或补偿动作。

## NATS 或事件延迟

检查 NATS 健康、消费者 lag 与死信记录。恢复消费者前不要重复手工发布同一业务事件。

## Secret 暴露疑虑

立即撤销 Vault/外部 Secret，更新 Secret Reference 指向的新版本，并检查审计记录。OpenKATE API 不应保存或返回 Secret 明文。
