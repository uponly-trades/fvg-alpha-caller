import json
import time


async def insert_audit(conn, user_id: int, action: str, payload: dict | None = None) -> None:
    await conn.execute(
        "INSERT INTO user_audit_log (user_id, action, payload, created_at) VALUES ($1, $2, $3::jsonb, $4)",
        user_id,
        action,
        json.dumps(payload or {}),
        int(time.time() * 1000),
    )
