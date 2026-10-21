import json, os, sys, requests

# --- 1. Read and clean secrets ---------------------------------------------
def clean(name):
    v = os.environ.get(name, "")
    # kill stray whitespace/newlines and wrapping quotes from copy-paste
    return v.strip().strip('"').strip("'").strip()

TOKEN = clean("TELEGRAM_BOT_TOKEN")
CHAT = clean("TELEGRAM_CHAT_ID")

def mask(s):
    return f"{s[:6]}...{s[-4:]} (len={len(s)})" if s else "(EMPTY)"

print("=== SECRETS ===")
print("TELEGRAM_BOT_TOKEN:", mask(TOKEN))
print("TELEGRAM_CHAT_ID  :", mask(CHAT))

if not TOKEN:
    sys.exit("FATAL: TELEGRAM_BOT_TOKEN is empty or not set in repo secrets.")
if ":" not in TOKEN:
    sys.exit("FATAL: TELEGRAM_BOT_TOKEN has no ':' - it is not a bot token.")
if any(c in TOKEN for c in " \n\r\t"):
    print("WARN: token still contains whitespace after cleaning.")

API = f"https://api.telegram.org/bot{TOKEN}"

# --- 2. Validate the token ------------------------------------------------
print("\n=== getMe ===")
r = requests.get(f"{API}/getMe", timeout=30)
print(r.status_code, r.text)
if not r.ok:
    sys.exit("FATAL: token rejected by Telegram. Re-create/re-copy it from @BotFather.")

def discover_chats():
    """List chat IDs that have messaged the bot recently."""
    print("\n=== getUpdates (finding your chat id) ===")
    r = requests.get(f"{API}/getUpdates", timeout=30)
    print(r.status_code, r.text)
    if not r.ok:
        print("Could not read updates.")
        return []
    found = []
    for upd in r.json().get("result", []):
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            entry = (chat["id"], chat.get("type"), chat.get("username") or chat.get("title") or chat.get("first_name"))
            if entry not in found:
                found.append(entry)
    if found:
        print("\n>>> CHAT IDS FOUND (copy the id into TELEGRAM_CHAT_ID):")
        for cid, ctype, name in found:
            print(f"    id={cid}  type={ctype}  name={name}")
    else:
        print("\n>>> No chats found. Open Telegram, message your bot /start, then re-run this workflow.")
    return found

# --- 3. Send ---------------------------------------------------------------
def send(text):
    if not CHAT:
        print("\n=== sendMessage skipped: CHAT id is empty ===")
        discover_chats()
        sys.exit("FATAL: set TELEGRAM_CHAT_ID, then re-run.")
    print("\n=== sendMessage ===")
    r = requests.post(f"{API}/sendMessage",
                      json={"chat_id": CHAT, "text": text},
                      timeout=30)
    print(r.status_code, r.text)
    if r.ok:
        print("SUCCESS: message delivered.")
        return
    # 400 usually means bad chat id -> try to recover it
    if "chat not found" in r.text or "chat_id" in r.text:
        print("\nChat id rejected. Attempting to discover the correct one...")
        discover_chats()
    sys.exit(f"FATAL: Telegram returned {r.status_code}. See description above.")

# --- 4. Run ----------------------------------------------------------------
with open("config.json") as f:
    cfg = json.load(f)

profiles = ", ".join(cfg["profiles"].keys())
send(f"Gig Hunter online. Profiles loaded: {profiles}. Sources: {len(cfg['sources'])}")
