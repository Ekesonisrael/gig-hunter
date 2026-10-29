import os
import json
import requests
import time
import urllib.parse
import sys

SERPER_KEY = os.environ.get("SERPER_API_KEY")
GROQ_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not all([SERPER_KEY, GROQ_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
    print("FATAL: Missing API keys in environment variables.")
    sys.exit(1)

SEARCH_QUERIES = [
    "Salon in Wuse 2 Abuja",
    "Shortlet apartment in Jabi Abuja"
]

def is_real_website(url):
    if not url: return False
    fake_sites = ["instagram.com", "facebook.com", "linktr.ee", "wa.me", "bit.ly"]
    return not any(site in url.lower() for site in fake_sites)

def generate_pitch(name, category):
    prompt = f"""
    You are a freelance web developer in Abuja. 
    Write a short, friendly WhatsApp message to the owner of "{name}" (a {category} in Abuja).
    Tell them you noticed they have great reviews on Google Maps, but they don't have a website yet.
    Offer them a 'Launch Package': A professional 1-page site to show their prices, plus Google profile optimization.
    Keep it under 50 words. Do not use emojis. Sound human and helpful, not salesy.
    """
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.1-8b-instant", # Updated model
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7
    }
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=20)
        if r.ok:
            return r.json()["choices"][0]["message"]["content"].strip()
        else:
            print(f"Groq API Error: {r.status_code} - {r.text}")
    except Exception as e:
        print(f"Groq Exception: {e}")
        
    return "Hi! I noticed you don't have a website yet. I build 1-page sites for local businesses in Abuja. Can I send you a demo?"

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # Added parse_mode HTML to fix the formatting!
    payload = {
        "chat_id": TELEGRAM_CHAT_ID, 
        "text": text, 
        "disable_web_page_preview": True,
        "parse_mode": "HTML" 
    }
    r = requests.post(url, json=payload)
    if not r.ok:
        print(f"Telegram Error: {r.text}")

def main():
    print("Starting Lead Finder...")
    for query in SEARCH_QUERIES:
        print(f"\nSearching Google Maps for: {query}")
        
        url = "https://google.serper.dev/maps"
        payload = json.dumps({"q": query, "gl": "ng"})
        headers = {'X-API-KEY': SERPER_KEY, 'Content-Type': 'application/json'}
        
        response = requests.post(url, headers=headers, data=payload)
        if not response.ok:
            print(f"Serper API Error: {response.text}")
            continue
            
        places = response.json().get("places", [])
        leads_found = 0
        
        for place in places:
            name = place.get("title")
            phone = place.get("phoneNumber")
            website = place.get("website", "")
            rating_count = place.get("ratingCount", 0)
            rating = place.get("rating", "N/A")
            address = place.get("address", "Unknown Address")
            
            if not phone: continue
            if rating_count < 10: continue 
            if is_real_website(website): continue
            
            category = "salon" if "salon" in query.lower() else "shortlet business"
            print(f"Found a lead: {name}. Generating pitch...")
            
            pitch = generate_pitch(name, category)
            wa_phone = phone.replace(" ", "").replace("+", "")
            wa_link = f"https://wa.me/{wa_phone}?text={urllib.parse.quote(pitch)}"
            
            # Changed ** to <b> for HTML formatting
            msg = (
                f"🎯 <b>[LEAD GEN] NEW BUSINESS FOUND</b>\n"
                f"🏢 <b>Name:</b> {name}\n"
                f"📞 <b>Phone:</b> {phone}\n"
                f"⭐ <b>Rating:</b> {rating} ({rating_count} reviews)\n"
                f"📍 <b>Address:</b> {address}\n\n"
                f"💬 <b>AI Pitch:</b>\n{pitch}\n\n"
                f"👉 <a href='{wa_link}'>CLICK HERE TO SEND ON WHATSAPP</a>"
            )
            
            send_telegram(msg)
            leads_found += 1
            print(f"✅ Sent {name} to Telegram!")
            time.sleep(2) 
            
        print(f"Done. Found {leads_found} leads for this query.")
        time.sleep(1)

if __name__ == "__main__":
    main()
