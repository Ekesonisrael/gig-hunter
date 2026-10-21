import json, os, sys, hashlib, re, time
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
MAX_AI_CHECKS = 25  

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

def score_and_pitch_job(title, summary):
    """Ask Groq to score the job and write a pitch."""
    prompt = f"""
    You are an expert recruiter filtering jobs for a freelancer based in Abuja, Nigeria.
    The freelancer has two profiles:
    1. Social Media Manager / Content Creator
    2. Vibe Coder / AI No-Code Developer (Framer, Lovable, Bolt, etc.)
    
    Portfolio Link: {PORTFOLIO}

    Job Title: {title}
    Job Description: {summary[:2000]}

    RULES FOR SCORING (0-10):
    - Score 0-2 if it is Fully On-site. We DO NOT WANT fully on-site jobs.
    - Score 8-10 if it is 100% Global Remote.
    - Score 8-10 if it is Hybrid AND located in Abuja, Nigeria (e.g., Wuse).
    - Score 0-2 if it strictly requires US/UK/EU work authorization or residency.
    - Score 0-2 if it's a Senior, Lead, or Principal role. We want Junior/Mid-level or Freelance.
    - Score 0-2 if it's unpaid, commission-only, or just "exposure".
    - Score 4-6 if it's ambiguous.

    IF THE SCORE IS 7 OR HIGHER:
    Write a short, punchy 3-sentence pitch/cover letter (max 60 words). 
    Mention that my portfolio is {PORTFOLIO}. Be direct, confident, and ready to send.

    Return ONLY a valid JSON object with this exact structure:
    {{"score": <integer 0-10>, "reason": "<short reason max 10 words>", "pitch": "<pitch text or empty string if score < 7>"}}
    """
    
    headers = {
        "Authorization": f"Bearer {GROQ_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.1
    }
    
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=20)
        if not r.ok:
            print(f"  [AI Error] Groq returned {r.status_code}: {r.text}")
            return 0, "AI Error", ""
        
        content = r.json()["choices"][0]["message"]["content"]
        content = re.sub(r"```json|```", "", content).strip()
        data = json.loads(content)
        return int(data.get("score", 0)), data.get("reason", ""), data.get("pitch", "")
    except Exception as e:
        print(f"  [AI Exception] {e}")
        return 0, "AI Exception", ""

def send(text):
    r = requests.post(f"{API}/sendMessage",
                      json={"chat_id": CHAT, "text": text, "disable_web_page_preview": True},
                      timeout=30)
    if not r.ok:
        print(f"Telegram {r.status_code}: {r.text}")

seen = load_seen()
new_items = []
ai_checks = 0

print(f"Keywords: {len(KEYWORDS)} | feeds: {len(cfg['feeds'])} | seen: {len(seen)}")

for feed in cfg["feeds"]:
    if not feed.get("enabled") or "PASTE_" in feed["url"]:
        continue
    try:
        resp = requests.get(feed["url"], timeout=30, headers={"User-Agent": "gig-hunter/1.0"})
        parsed = feedparser.parse(resp.content)
        
        for e in parsed.entries:
            iid = item_id(e)
            if iid in seen:
                continue
            seen.add(iid)
            
            title = strip_html(e.get("title", ""))
            summary = strip_html(e.get("summary", ""))
            
            if not matches(f"{title} {summary}"):
                continue
                
            if ai_checks >= MAX_AI_CHECKS:
                print("Reached MAX_AI_CHECKS limit. Stopping search for this run.")
                break
                
            print(f"Checking: {title}")
            score, reason, pitch = score_and_pitch_job(title, summary)
            ai_checks += 1
            time.sleep(1) 
            
            print(f"  -> Score: {score} ({reason})")
            
            if score >= 7:
                new_items.append({
                    "source": feed["name"],
                    "title": title or "(no title)",
                    "link": e.get("link", ""),
                    "score": score,
                    "reason": reason,
                    "pitch": pitch
                })
                
    except Exception as ex:
        print(f"FAIL  {feed['name']}: {ex}")

save_seen(seen)

if TEST:
    print(f"\nTEST MODE: {len(new_items)} good matches found, nothing sent.")
    for i in new_items[:MAX_PER_RUN]:
        print(f"  [{i['score']}/10] {i['title']} - {i['reason']}")
        print(f"  PITCH: {i['pitch']}\n")
    sys.exit(0)

if not new_items:
    print("No good matches found. Staying quiet.")
    sys.exit(0)

batch = new_items[:MAX_PER_RUN]
lines, chunk = [], ""
for i in batch:
    line = (
        f"• [{i['score']}/10] {i['title']}\n"
        f"Source: {i['source']}\n"
        f"Reason: {i['reason']}\n"
        f"🔗 {i['link']}\n\n"
        f"📝 **Generated Pitch:**\n"
        f"{i['pitch']}\n\n"
        f"--------------------\n"
    )
    if len(chunk) + len(line) > 3800:
        lines.append(chunk); chunk = ""
    chunk += line
if chunk:
    lines.append(chunk)

for c in lines:
    send(f"Gig Hunter — {len(batch)} high-quality match(es)\n\n{c}")

print(f"Sent {len(batch)} matches.")
