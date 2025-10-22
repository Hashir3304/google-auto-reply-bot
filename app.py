import os, time, requests
from openai import OpenAI

OPENAI_KEY = os.getenv("OPENAI_KEY")
GOOGLE_ACCESS_TOKEN = os.getenv("GOOGLE_ACCESS_TOKEN")
PLACE_ID = os.getenv("PLACE_ID")

client = OpenAI(api_key=OPENAI_KEY)

def get_reviews():
    url = f"https://mybusiness.googleapis.com/v4/accounts/accountId/locations/{PLACE_ID}/reviews"
    headers = {"Authorization": f"Bearer {GOOGLE_ACCESS_TOKEN}"}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        return r.json().get("reviews", [])
    return []

def post_reply(review_id, reply_text):
    url = f"https://mybusiness.googleapis.com/v4/accounts/accountId/locations/{PLACE_ID}/reviews/{review_id}/reply"
    headers = {
        "Authorization": f"Bearer {GOOGLE_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {"comment": reply_text}
    requests.post(url, headers=headers, json=data)

def generate_reply(review_text, rating):
    prompt = f"Write a short, kind, professional reply to a {rating}-star Google review: '{review_text}'"
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content.strip()

def main():
    seen = set()
    while True:
        for r in get_reviews():
            if r["reviewId"] not in seen:
                seen.add(r["reviewId"])
                text, rating = r["comment"], r["starRating"]
                reply = generate_reply(text, rating)
                post_reply(r["reviewId"], reply)
                print(f"✅ Replied to review {r['reviewId']}")
        time.sleep(3600)

if __name__ == "__main__":
    main()
