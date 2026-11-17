import ollama

r = ollama.chat(
    model="qwen3.5:4b",
    think=False,
    messages=[{"role": "user", "content": "Say hi in 5 words"}],
)
print(r["message"]["content"])
