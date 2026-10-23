import json, os, sys, hashlib, re, time, datetime
import requests, feedparser

def clean(name):
    return os.environ.get(name, "").strip().strip('"').strip("'").strip()

TOKEN = clean("TELEGRAM_BOT_TOKEN")
CHAT  = clean("TELEGRAM_CHAT_ID")
GROQ_KEY = clean("GROQ_API_KEY")
TEST  = clean("FEED_TEST") == "1"
API   = f"https://api.telegram.org/bot{TOKEN}"
SEEN_FILE = "seen.json"
MAX_PER_RUN = 10
MAX_AI_CHECKS = 10  # Reverted back to 10 for Groq limits
MAX_CHECKS_PER_FEED = 3 

if not TOKEN or not CHAT or not GROQ_KEY:
    sys.exit("FATAL: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, or GROQ_API_KEY missing.")

with open("config.json") as f:
    cfg = json.load(f)

KEYWORDS = sorted({k.lower() for p in cfg["profiles"].values() for k in p["keywords"]})
PORTFOLIO = cfg["profiles"]["vibe"].get("portfolio_link", "")

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

def score_and_pitch_job(title, summary, location):
    prompt = f"""
    You are an expert recruiter filtering jobs for a freelancer in Abuja, Nigeria.
    The freelancer has two profiles: Social Media Manager AND Vibe Coder / AI No-Code Developer.
    Portfolio Link: {PORTFOLIO}

    Job Title: {title}
    Job Location: {location}
    Job Description: {summary[:1000]}

    RULES FOR SCORING (0-10):
    - Score 0-2 if Fully On-site. We DO NOT WANT fully on-site jobs.
    - Score 8-10 if 100% Global Remote.
    - Score 8-10 if Hybrid AND located in Abuja, Nigeria.
    - Score 0-2 if strictly requires US/UK/EU work authorization.
    - Score 0-2 if Senior, Lead, or Principal role.
    - Score 0-2 if unpaid or commission-only.
    - **CRITICAL:** Score 0-2 if pure Sales, Account Management, or Business Development.
    - **CRITICAL:** Score 0-2 if Enterprise IT, heavy backend, or deep coding (React, C++).
    - If location is "Remote" but country is not explicitly stated, score exactly a 7.

    IF THE SCORE IS 7 OR HIGHER:
    Write a short, punchy 3-sentence pitch (max 60 words). 
    ONLY pitch the skills that match the job title. Mention {PORTFOLIO}. Do NOT invent experience.

    Return ONLY a valid JSON object with this exact structure:
    {{"score": <integer 0-10>, "reason": "<short reason max 10 words>", "pitch": "<pitch text or empty string if score < 7>"}}
    """
    
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.1
    }
    
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=20)
        if not r.ok:
            print(f"  [AI Error] Groq returned {r.status_code}")
            return 0, "AI Error", "", False
        
        content = r.json()["choices"][0]["message"]["content"]
        content = re.sub(r"```json|```", "", content).strip()
        data = json.loads(content)
        return int(data.get("score", 0)), data.get("reason", ""), data.get("pitch", ""), True
    except Exception as e:
        print(f"  [AI Exception] {e}")
        return 0, "AI Exception", "", False

def send(text):
    r = requests.post(f"{API}/sendMessage",
                      json={"chat_id": CHAT, "text": text, "disable_web_page_preview": True},
                      timeout=30)
    if not r.ok:
        print(f"Telegram {r.status_code}: {r.text}")
        return False
    return True

seen = load_seen()
new_items = []
total_ai_checks = 0

print(f"Keywords: {len(KEYWORDS)} | feeds: {len(cfg['feeds'])} | seen: {len(seen)} | TEST MODE: {TEST}")
headers_ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}

for feed in cfg["feeds"]:
    if not feed.get("enabled") or "PASTE_" in feed["url"]:
        continue
    
    feed_checks = 0
    seen_count = 0
    stale_count = 0
    no_date_count = 0
    keyword_hits = 0
    
    try:
        resp = requests.get(feed["url"], timeout=30, headers=headers_ua)
        parsed = feedparser.parse(resp.content)
        entries = parsed.entries
        
        if len(entries) > 0:
            print(f"DEBUG KEYS for {feed['name']}: {list(entries[0].keys())}")
        
        for e in entries:
            iid = item_id(e)
            
            # In TEST mode, bypass the seen check so we actually test the feeds
            if not TEST and iid in seen:
                seen_count += 1
                continue
                
            date_field = None
            if hasattr(e, "published_parsed") and e.published_parsed:
                date_field = e.published_parsed
            elif hasattr(e, "updated_parsed") and e.updated_parsed:
                date_field = e.updated_parsed

            if not date_field:
                no_date_count += 1
                continue
                
            try:
                pub_dt = datetime.datetime(*date_field[:6], tzinfo=datetime.timezone.utc)
                now_dt = datetime.datetime.now(datetime.timezone.utc)
                age = now_dt - pub_dt
                if age.total_seconds() > 172800:
                    stale_count += 1
                    continue
            except Exception:
                no_date_count += 1
                continue
                
            title = strip_html(e.get("title", ""))
            summary = strip_html(e.get("summary", ""))
            
            # Using only explicit location field if it exists
            location = e.get("location", "")
            
            if not matches(f"{title} {summary} {location}"):
                continue
                
            keyword_hits += 1
            
            if total_ai_checks >= MAX_AI_CHECKS or feed_checks >= MAX_CHECKS_PER_FEED:
                continue
                
            print(f"Checking: {title}")
            score, reason, pitch, ai_success = score_and_pitch_job(title, summary, location)
            total_ai_checks += 1
            feed_checks += 1
            time.sleep(1.5) 
            
            print(f"  -> Score: {score} ({reason})")
            
            if ai_success:
                # Only add to seen if not in test mode, so test mode doesn't burn jobs
                if not TEST:
                    seen.add(iid)
                if score >= 7:
                    new_items.append({
                        "id": iid,
                        "source": feed["name"],
                        "title": title or "(no title)",
                        "link": e.get("link", ""),
                        "score": score,
                        "reason": reason,
                        "pitch": pitch
                    })
        
        print(f"Feed: {feed['name']} | Status: {resp.status_code} | Entries: {len(entries)} | Seen: {seen_count} | Stale: {stale_count} | NoDate: {no_date_count} | Keywords: {keyword_hits}")
        
    except Exception as ex:
        print(f"FAIL  {feed['name']}: {ex}")

if TEST:
    print(f"\nTEST MODE: {len(new_items)} good matches found, nothing sent. Seen list NOT saved.")
    for i in new_items[:MAX_PER_RUN]:
        print(f"  [{i['score']}/10] {i['title']} - {i['reason']}")
    sys.exit(0)

if not new_items:
    print("No good matches found. Staying quiet.")
    save_seen(seen)
    sys.exit(0)

batch = new_items[:MAX_PER_RUN]
lines, chunk = [], ""
for i in batch:
    line = (
        f"• [{i['score']}/10] {i['title']}\n"
        f"Source: {i['source']}\n"
        f"Reason: {i['reason']}\n"
        f"Link: {i['link']}\n\n"
        f"Generated Pitch:\n"
        f"{i['pitch']}\n\n"
        f"--------------------\n"
    )
    if len(chunk) + len(line) > 3800:
        lines.append(chunk); chunk = ""
    chunk += line
if chunk:
    lines.append(chunk)

send_success = True
for c in lines:
    if not send(f"Gig Hunter - {len(batch)} high-quality match(es)\n\n{c}"):
        send_success = False

if send_success:
    save_seen(seen)
    print(f"Sent {len(batch)} matches. Seen list saved.")
else:
    print("Telegram failed. Seen list NOT saved so jobs will retry next run.")
