import os
from app import fetch_gemini

print("Key starts with:", os.environ.get("GEMINI_API_KEY", "")[:5])
data, err = fetch_gemini("Reliance")
print("DATA:", data)
print("ERR:", err)
