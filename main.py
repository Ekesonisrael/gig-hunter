import json, os, requests

def send(text):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": text},
        timeout=30,
    )
    r.raise_for_status()

with open("config.json") as f:
    cfg = json.load(f)

profiles = ", ".join(cfg["profiles"].keys())
send(f"Gig Hunter online. Profiles loaded: {profiles}. Sources: {len(cfg['sources'])}")
