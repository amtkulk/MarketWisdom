import os
from google import genai

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
client = genai.Client(api_key=GEMINI_API_KEY)

print("Checking available models...")
try:
    for model in client.models.list():
        print(f"Model name: {model.name}")
except Exception as e:
    print(f"Error listing models: {e}")
