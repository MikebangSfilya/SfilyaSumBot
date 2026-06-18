import json


def count_recent_logs(raw_logs: list[str], current_ts: float, period_seconds: int) -> int:
    cutoff_ts = current_ts - period_seconds
    count = 0
    for raw_log in raw_logs:
        try:
            data = json.loads(raw_log)
        except json.JSONDecodeError:
            continue
        if (message_ts := data.get("ts")) is not None and message_ts >= cutoff_ts:
            count += 1
    return count
