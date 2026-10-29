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
SHEETS_WEBHOOK_URL = os.environ.get("SHEETS_WEBHOOK_URL")

if not all([SERPER_KEY, GROQ_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, SHEETS_WEBHOOK_URL]):
    sys.exit("Missing keys.")

SEARCH_QUERIES = ["Salon in Wuse 2 Abuja", "Shortlet apartment in Jabi Abuja"]

def is_real_website(url):
    if not url: return False
    fake = ["instagram.com", "facebook.com", "linktr.ee", "wa.me", "bit.ly"]
    return not any(site in url.lower() for site in fake)

def generate_pitch(name, category):
    prompt = f"Write a short, friendly WhatsApp message to the owner of '{name}' (a {category} in Abuja). Tell them they have great reviews but no website. Offer a 'Launch Package': A 1-page site + Google profile optimization. Keep it under 50 words. Sound helpful."
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    payload = {"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": prompt}], "temperature": 0.7}
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=20)
        if r.ok: return r.json()["choices"][0]["message"]["content"].strip()
    except: pass
    return "Hi! I noticed you don't have a website yet. I build 1-page sites for local businesses in Abuja. Can I send you a demo?"

def send_telegram(text, place_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    # Create the interactive buttons
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Contacted", "callback_data": f"contacted_{place_id}"},
                {"text": "💬 Replied", "callback_data": f"replied_{place_id}"}
            ],
            [
                {"text": "💰 WON", "callback_data": f"won_{place_id}"},
                {"text": "❌ LOST", "callback_data": f"lost_{place_id}"}
            ]
        ]
    }
    
    payload = {
        "chat_id": TELEGRAM_CHAT_ID, 
        "text": text, 
        "disable_web_page_preview": True,
        "parse_mode": "HTML",
        "reply_markup": json.dumps(reply_markup) # Attach buttons
    }
    requests.post(url, json=payload)

def log_to_sheet(name, phone, address, rating, place_id):
    payload = {"name": name, "phone": phone, "address": address, "rating": rating, "placeId": place_id}
    try:
        requests.post(SHEETS_WEBHOOK_URL, json=payload, timeout=10)
    except Exception as e:
        print(f"Sheet Exception: {e}")

def main():
    print("Starting Lead Finder...")
    for query in SEARCH_QUERIES:
        url = "https://google.serper.dev/maps"
        payload = json.dumps({"q": query, "gl": "ng"})
        headers = {'X-API-KEY': SERPER_KEY, 'Content-Type': 'application/json'}
        
        response = requests.post(url, headers=headers, data=payload)
        if not response.ok: continue
            
        places = response.json().get("places", [])
        
        for place in places:
            name = place.get("title")
            phone = place.get("phoneNumber")
            website = place.get("website", "")
            place_id = place.get("placeId", "N/A") # Needed for buttons
            rating_count = place.get("ratingCount", 0)
            rating = place.get("rating", "N/A")
            address = place.get("address", "Unknown Address")
            
            if not phone or rating_count < 10 or is_real_website(website):
                continue
            
            category = "salon" if "salon" in query.lower() else "shortlet"
            pitch = generate_pitch(name, category)
            wa_phone = phone.replace(" ", "").replace("+", "")
            wa_link = f"https://wa.me/{wa_phone}?text={urllib.parse.quote(pitch)}"
            
            log_to_sheet(name, phone, address, f"{rating} ({rating_count})", place_id)
            
            msg = (
                f"🎯 <b>[LEAD GEN] NEW BUSINESS FOUND</b>\n"
                f"🏢 <b>Name:</b> {name}\n"
                f"📞 <b>Phone:</b> {phone}\n"
                f"⭐ <b>Rating:</b> {rating} ({rating_count} reviews)\n"
                f"📍 <b>Address:</b> {address}\n\n"
                f"💬 <b>AI Pitch:</b>\n{pitch}\n\n"
                f"👉 <a href='{wa_link}'>CLICK HERE TO SEND ON WHATSAPP</a>"
            )
            
            send_telegram(msg, place_id)
            time.sleep(2) 

if __name__ == "__main__":
    main()
