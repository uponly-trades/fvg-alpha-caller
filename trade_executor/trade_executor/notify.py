import json


def _quote_literal(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


async def notify(conn, channel: str, payload: dict) -> None:
    if not channel.replace("_", "").isalnum():
        raise ValueError("channel must be alphanumeric/underscore")
    body = _quote_literal(json.dumps(payload))
    await conn.execute(f"NOTIFY {channel}, {body}")
