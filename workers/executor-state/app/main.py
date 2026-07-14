from openkate_common.worker import capability_heartbeat
print(capability_heartbeat("executor-state", ["state.postgresql.read_only"]))
