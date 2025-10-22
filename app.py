from flask import Flask, jsonify, request
from openai import OpenAI
import os
import requests

app = Flask(__name__)

# ✅ Load environment variables
OPENAI_KEY = os.getenv("OPENAI_KEY")
GOOGLE_ACCESS_TOKEN = os.getenv("GOOGLE_ACCESS_TOKEN")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")
PLACE_ID = os.getenv("PLACE_ID")

# ✅ Initialize OpenAI client (new SDK format)
client = OpenAI(api_key=OPENAI_KEY)

@app.route('/')
def home():
    return "✅ Pawsy Prints Auto-Reply Bot is running!"

@app.route('/test', methods=['GET'])
def test():
    user_text = request.args.get("text", "The product is great!")
    stars = request.args.get("stars", "5")

    # ✅ Use new client format for chat completions
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are Pawsy Prints’ friendly customer service assistant."},
            {"role": "user", "content": f"A {stars}-star customer review says: '{user_text}'. Write a short, kind reply."}
        ],
    )

    reply = response.choices[0].message.content.strip()

    return jsonify({
        "customer_review": user_text,
        "ai_reply": reply
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
