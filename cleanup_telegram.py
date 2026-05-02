import requests
import time

BOT_TOKEN = "8648135703:AAGESrLIN336vb1Estd4BroWeTM-Sg1luQM"
CHAT_ID   = "-1003534109980"
BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"


def delete_message(msg_id: int):
    resp = requests.post(
        f"{BASE}/deleteMessage",
        json={"chat_id": CHAT_ID, "message_id": msg_id},
        timeout=15,
    )
    return resp.json()


def send_test():
    resp = requests.post(
        f"{BASE}/sendMessage",
        json={"chat_id": CHAT_ID, "text": "test"},
        timeout=15,
    )
    return resp.json()


def main():
    # First send a test message so we know the approximate message_id range
    r = send_test()
    if not r.get("ok"):
        print("Cannot send message:", r)
        return
    max_id = r["result"]["message_id"]
    print(f"Test sent, max_id ≈ {max_id}")

    # Delete backwards from max_id down to max_id - 50
    ok = fail = 0
    for mid in range(max_id, max_id - 60, -1):
        r = delete_message(mid)
        if r.get("ok"):
            ok += 1
        else:
            fail += 1
            if "message to delete not found" not in r.get("description", ""):
                print(f"  fail msg_id={mid}: {r.get('description')}")
        time.sleep(0.05)
    print(f"Done: {ok} deleted, {fail} failed.")


if __name__ == "__main__":
    main()
