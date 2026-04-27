import os
import json
from google import genai
from google.genai import types

api_key = os.environ.get("GEMINI_API_KEY", "")
print("Key exists:", bool(api_key))
client = genai.Client(api_key=api_key)

MODELS = ["gemini-2.0-flash-lite", "gemini-2.0-flash", "gemini-2.5-flash"]
for model in MODELS:
    print(f"Testing {model}...")
    try:
        cfg_args = {"temperature": 0.2, "max_output_tokens": 100}
        resp = client.models.generate_content(
            model=model,
            contents="Say hi",
            config=types.GenerateContentConfig(**cfg_args)
        )
        print("Success:", resp.text)
    except Exception as ex:
        print("Exception:", type(ex).__name__, str(ex))
