import json, os, sys, hashlib, re, time, datetime
import requests, feedparser

# ---------- env ----------
def clean(name):
    return os.environ.get(name, "").strip().strip('"').strip("'").strip()

TOKEN     = clean("TELEGRAM_BOT_TOKEN")
CHAT      = clean("TELEGRAM_CHAT_ID")
GROQ_KEY  = clean("GROQ_API_KEY")
TEST      = clean("FEED_TEST") == "1"
API       = f"https://api.telegram.org/bot{TOKEN}"

SEEN_FILE          = "seen.json"
MAX_PER_RUN        = 10
MAX_AI_CHECKS      = 30        # raised from 10
MAX_CHECKS_PER_FEED = 8        # raised from 3
MAX_AGE_HOURS      = 168       # 7 days, was 2
MAX_SEEN_ENTRIES   = 5000

if not TOKEN or not CHAT or not GROQ_KEY:
    sys.exit("FATAL: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, or GROQ_API_KEY missing.")

with open("config.json") as f:
    cfg = json.load(f)

PROFILES         = cfg["profiles"]
PROFILE_KEYWORDS = {n: [k.lower() for k in p["keywords"]] for n, p in PROFILES.items()}
PROFILE_SKILLS   = {n: p.get("skills", "").strip() for n, p in PROFILES.items()}
PORTFOLIO = (
    PROFILES.get("vibe",   {}).get("portfolio_link", "")
    or PROFILES.get("social", {}).get("portfolio_link", "")
)

EXCLUDE_TITLE = re.compile(
    r"\b(senior|sr\.?|lead|principal|staff|head\s+of|vp|chief|director|"
    r"account\s+executive|account\s+manager|sales|sdr|bdr|business\s+development)\b",
    re.I,
)

# ---------- seen ----------
def load_seen():
    try:
        with open(SEEN_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            return {str(i): now for i in data}
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}

def save_seen(seen):
    if len(seen) > MAX_SEEN_ENTRIES:
        seen = dict(sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:MAX_SEEN_ENTRIES])
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f, indent=0)

def mark_seen(seen, iid):
    seen[iid] = datetime.datetime.now(datetime.timezone.utc).isoformat()

# ---------- feed helpers ----------
def item_id(entry):
    key = entry.get("link") or entry.get("id") or entry.get("title", "")
    return hashlib.sha1(key.encode("utf-8", "ignore")).hexdigest()

def strip_html(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()

def matches_profile(title, summary):
    t = title.lower()
    for name, kws in PROFILE_KEYWORDS.items():
        if any(k in t for k in kws):
            return name
    if len(title) < 20:  # opaque title fallback
        s = summary[:200].lower()
        for name, kws in PROFILE_KEYWORDS.items():
            if any(k in s for k in kws):
                return name
    return None

def get_location(e):
    if hasattr(e, "himalayasjobs_locationrestriction"): return e.himalayasjobs_locationrestriction
    if hasattr(e, "location"):                          return e.location
    if hasattr(e, "job_listing_location"):              return e.job_listing_location
    if hasattr(e, "region") or hasattr(e, "country"):
        return f"{getattr(e,'region','')} {getattr(e,'country','')}".strip()
    return "Not specified"

# ---------- LLM ----------
EXTRACTION_PROMPT = """You are a job-listing analyst. Extract structured facts from a job posting and return ONLY valid JSON.

Freelancer: Abuja, Nigeria. Portfolio: {portfolio}

Two candidate profiles:
- "social": Social Media Manager. Real skills: {social_skills}
- "vibe":   Vibe Coder / AI No-Code Developer. Real skills: {vibe_skills}

JOB TITLE: {title}
JOB LOCATION (raw): {location}
JOB DESCRIPTION (first 1200 chars):
{summary}

Return exactly this JSON schema:
{{
  "primary_profile": "social" | "vibe" | "neither",
  "location_class":  "global_remote" | "remote_unspecified" | "hybrid_abuja" | "hybrid_other" | "onsite_abuja" | "onsite_other" | "region_locked",
  "seniority":       "junior" | "mid" | "senior" | "lead" | "principal" | "director" | "unknown",
  "is_paid":         true | false,
  "reason":          "<max 10 words>",
  "pitch":           "<3-sentence pitch, max 60 words, using ONLY the skills listed for the chosen primary_profile. Mention the portfolio URL. Do NOT invent experience. Empty string if primary_profile is 'neither' OR seniority is senior/lead/principal/director.>"
}}

RULES FOR primary_profile:
- "social" only if the role is primarily social media / content / paid-social work.
- "vibe"   only if the role is primarily web build / no-code / AI automation / landing-page work.
- "neither" if it is sales, account management, business development, enterprise IT, backend engineering, or any role neither profile could plausibly deliver.
- If both fit equally: prefer "social" when the title mentions social/content/marketing, else "vibe".

RULES FOR location_class:
- "global_remote": explicitly worldwide / anywhere / global / no geographic restriction.
- "remote_unspecified": remote, scope unclear, or field just says "Remote".
- "hybrid_abuja": hybrid, Abuja, Nigeria.
- "hybrid_other": hybrid, not Abuja.
- "onsite_abuja": fully on-site in Abuja.
- "onsite_other": fully on-site elsewhere.
- "region_locked": requires US/UK/EU/Canada work auth, country-restricted, or a timezone that excludes West Africa (e.g. "must work US Eastern").

RULES FOR seniority:
- "senior": title says senior/sr., or 5+ years leading projects.
- "lead"/"principal"/"director": corresponding level in title/description.
- "mid": default individual contributor.
- "junior": explicit entry-level / no-experience.
- "unknown": cannot tell.

RULES FOR is_paid:
- false if commission-only, equity-only, unpaid, revenue-share-only, or internship without stipend.
- true otherwise.
"""

def call_groq(payload):
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    last = None
    for _ in range(3):
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                              headers=headers, json=payload, timeout=25)
        except Exception as e:
            last = e; time.sleep(2); continue
        if r.status_code == 429:
            wait = 5
            try: wait = int(r.headers.get("Retry-After", 5))
            except Exception: pass
            time.sleep(min(wait, 30))
            last = r; continue
        return r
    return last

def analyze_job(profile_hint, title, summary, location):
    prompt = EXTRACTION_PROMPT.format(
        portfolio     = PORTFOLIO,
        social_skills = PROFILE_SKILLS.get("social", ""),
        vibe_skills   = PROFILE_SKILLS.get("vibe", ""),
        title         = title,
        location      = location,
        summary       = summary[:1200],
    )
    payload = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
    }
    r = call_groq(payload)
    if r is None or not getattr(r, "ok", False):
        status = getattr(r, "status_code", "no-response")
        return None, f"AI Error {status}", ""
    try:
        content = r.json()["choices"][0]["message"]["content"]
        content = re.sub(r"```json|```", "", content).strip()
        data = json.loads(content)
    except Exception as e:
        return None, f"Parse Error: {e}", ""
    return data, data.get("reason", ""), data.get("pitch", "")

# Score is computed in code from extracted fields — no LLM arithmetic.
def compute_score(d):
    if not isinstance(d, dict):                                                  return 0
    if d.get("primary_profile") not in ("social", "vibe"):                       return 0
    if d.get("is_paid") is not True:                                             return 0
    if d.get("seniority") in ("senior", "lead", "principal", "director"):        return 2
    loc = d.get("location_class")
    if loc == "global_remote":                                                   return 9
    if loc == "hybrid_abuja":                                                    return 9
    if loc == "remote_unspecified":                                              return 7
    if loc == "hybrid_other":                                                    return 4
    if loc in ("onsite_abuja", "onsite_other", "region_locked"):                 return 2
    return 4

# ---------- telegram ----------
def send(text):
    r = requests.post(f"{API}/sendMessage",
                      json={"chat_id": CHAT, "text": text, "disable_web_page_preview": True},
                      timeout=30)
    if not r.ok:
        print(f"Telegram {r.status_code}: {r.text}")
        return False
    return True

# ---------- main ----------
seen = load_seen()
new_items = []
total_ai_checks = 0

print(f"TEST={TEST} feeds={len(cfg['feeds'])} seen={len(seen)}")
headers_ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}

for feed in cfg["feeds"]:
    if not feed.get("enabled") or "PASTE_" in feed["url"]:
        continue

    stats = dict(entries=0, seen=0, no_date=0, too_old=0, excluded=0,
                 no_kw=0, capped=0, ai_fail=0, scored=0, high=0)
    feed_checks = 0

    try:
        resp = requests.get(feed["url"], timeout=30, headers=headers_ua)
        parsed = feedparser.parse(resp.content)
        entries = parsed.entries
        stats["entries"] = len(entries)

        candidates = []
        now_dt = datetime.datetime.now(datetime.timezone.utc)

        for e in entries:
            iid = item_id(e)
            if iid in seen:
                stats["seen"] += 1; continue

            date_field = None
            if getattr(e, "published_parsed", None): date_field = e.published_parsed
            elif getattr(e, "updated_parsed", None): date_field = e.updated_parsed
            if not date_field:
                stats["no_date"] += 1; continue
            try:
                pub_dt = datetime.datetime(*date_field[:6], tzinfo=datetime.timezone.utc)
            except Exception:
                stats["no_date"] += 1; continue

            if (now_dt - pub_dt).total_seconds() > MAX_AGE_HOURS * 3600:
                stats["too_old"] += 1; continue

            title = strip_html(e.get("title", ""))
            if EXCLUDE_TITLE.search(title):
                stats["excluded"] += 1; continue

            summary = strip_html(e.get("summary", ""))
            location = get_location(e)

            matched_profile = matches_profile(title, summary)
            if not matched_profile:
                stats["no_kw"] += 1; continue

            candidates.append((pub_dt, e, iid, title, summary, location, matched_profile))

        candidates.sort(key=lambda x: x[0], reverse=True)  # newest first

        for pub_dt, e, iid, title, summary, location, matched_profile in candidates:
            if total_ai_checks >= MAX_AI_CHECKS or feed_checks >= MAX_CHECKS_PER_FEED:
                stats["capped"] += 1; continue

            print(f"[{feed['name']}] Checking: {title} | loc={location} | kw={matched_profile}")
            data, reason, pitch = analyze_job(matched_profile, title, summary, location)
            total_ai_checks += 1
            feed_checks    += 1
            time.sleep(1.2)

            if data is None:
                stats["ai_fail"] += 1
                print(f"  -> AI FAIL ({reason})")
                continue

            score = compute_score(data)
            stats["scored"] += 1
            print(f"  -> Score: {score} | profile={data.get('primary_profile')} "
                  f"loc={data.get('location_class')} sen={data.get('seniority')} "
                  f"paid={data.get('is_paid')} | {reason}")

            if score >= 7:
                stats["high"] += 1
                new_items.append({
                    "id": iid,
                    "source": feed["name"],
                    "title": title or "(no title)",
                    "link": e.get("link", ""),
                    "score": score,
                    "reason": reason,
                    "pitch": pitch or "",
                    "profile": data.get("primary_profile"),
                })

    except Exception as ex:
        print(f"FAIL {feed['name']}: {ex}")
    finally:
        print(f"  [{feed['name']}] stats: {stats}")

if TEST:
    print(f"\nTEST MODE: {len(new_items)} matches (not sent). Seen NOT saved.")
    for i in new_items:
        print(f"  [{i['score']}/10] [{i['profile']}] {i['title']} — {i['reason']}")
    sys.exit(0)

if not new_items:
    print("No matches. Saving seen and exiting.")
    save_seen(seen)
    sys.exit(0)

batch = new_items[:MAX_PER_RUN]

# Build chunks and track which item IDs belong to each chunk,
# so a partial Telegram failure only re-queues the failed chunk.
chunks, cur_text, cur_ids, cur_len = [], [], [], 0
for item in batch:
    line = (
        f"• [{item['score']}/10] [{item['profile']}] {item['title']}\n"
        f"Source: {item['source']}\n"
        f"Reason: {item['reason']}\n"
        f"Link: {item['link']}\n\n"
        f"Pitch:\n{item['pitch']}\n\n"
        f"--------------------\n"
    )
    if cur_len + len(line) > 3800:
        if cur_text:
            chunks.append({"text": "".join(cur_text), "ids": cur_ids})
        cur_text, cur_ids, cur_len = [], [], 0
    cur_text.append(line); cur_ids.append(item["id"]); cur_len += len(line)
if cur_text:
    chunks.append({"text": "".join(cur_text), "ids": cur_ids})

total_sent = 0
for i, chunk in enumerate(chunks, 1):
    header = f"Gig Hunter — {len(batch)} match(es) [chunk {i}/{len(chunks)}]\n\n"
    if send(header + chunk["text"]):
        for iid in chunk["ids"]:
            mark_seen(seen, iid)
        total_sent += len(chunk["ids"])
    else:
        print(f"  Chunk {i} failed; {len(chunk['ids'])} items will retry next run.")

save_seen(seen)
print(f"Sent {total_sent}/{len(batch)}. Seen now {len(seen)} entries.")
