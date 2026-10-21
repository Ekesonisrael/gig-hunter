import json, os, sys, hashlib, re
import requests, feedparser

def clean(name):
    return os.environ.get(name, "").strip().strip('"').strip("'").strip()

TOKEN = clean("TELEGRAM_BOT_TOKEN")
CHAT  = clean("TELEGRAM_CHAT_ID")
TEST  = clean("FEED_TEST") == "1"
API   = f"https://api.telegram.org/bot{TOKEN}"
SEEN_FILE = "seen.json"
MAX_PER_RUN = 20

if not TOKEN or not CHAT:
    sys.exit("FATAL: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set.")

with open("config.json") as f:
    cfg = json.load(f)

KEYWORDS = sorted({k.lower() for p in cfg["profiles"].values() for k in p["keywords"]})

def load_seen():
    try:
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(seen), f, indent=0)

def item_id(entry):
    key = entry.get("link") or entry.get("id") or entry.get("title", "")
    return hashlib.sha1(key.encode("utf-8", "ignore")).hexdigest()

def strip_html(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()

def matches(text):
    t = text.lower()
    return any(k in t for k in KEYWORDS)

def send(text):
    r = requests.post(f"{API}/sendMessage",
                      json={"chat_id": CHAT, "text": text, "disable_web_page_preview": True},
                      timeout=30)
    if not r.ok:
        sys.exit(f"Telegram {r.status_code}: {r.text}")
    return r

seen = load_seen()
new_items = []

print(f"Keywords: {len(KEYWORDS)} | feeds: {len(cfg['feeds'])} | seen: {len(seen)}")

for feed in cfg["feeds"]:
    if not feed.get("enabled"):
        print(f"SKIP  {feed['name']} (disabled)")
        continue
    if "PASTE_" in feed["url"]:
        print(f"SKIP  {feed['name']} (url not set)")
        continue
    try:
        resp = requests.get(feed["url"], timeout=30,
                            headers={"User-Agent": "gig-hunter/1.0"})
        parsed = feedparser.parse(resp.content)
        entries = parsed.entries
        hits = 0
        for e in entries:
            iid = item_id(e)
            if iid in seen:
                continue
            seen.add(iid)                      # mark seen even if not matched
            title = strip_html(e.get("title", ""))
            summary = strip_html(e.get("summary", ""))
            if not matches(f"{title} {summary}"):
                continue
            new_items.append({
                "source": feed["name"],
                "title": title or "(no title)",
                "link": e.get("link", ""),
            })
            hits += 1
        print(f"OK    {feed['name']}: {len(entries)} entries, {hits} new matches")
    except Exception as ex:
        print(f"FAIL  {feed['name']}: {ex}")

save_seen(seen)

if TEST:
    print(f"\nTEST MODE: {len(new_items)} matches, nothing sent.")
    for i in new_items[:MAX_PER_RUN]:
        print(f"  [{i['source']}] {i['title']}")
    sys.exit(0)

if not new_items:
    print("No new matches. Staying quiet.")
    sys.exit(0)

batch = new_items[:MAX_PER_RUN]
lines, chunk = [], ""
for i in batch:
    line = f"• {i['title']}\n{i['source']}\n{i['link']}\n\n"
    if len(chunk) + len(line) > 3800:
        lines.append(chunk); chunk = ""
    chunk += line
if chunk:
    lines.append(chunk)

for c in lines:
    send(f"Gig Hunter — {len(batch)} new match(es)\n\n{c}")

print(f"Sent {len(batch)} matches ({len(new_items) - len(batch)} deferred).")
