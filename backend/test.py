import os
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# .env ao @ D:\kahie (iray ambony ny backend)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

client = OpenAI(
    api_key=os.environ["MODEL_API_KEY"],
    base_url=os.environ.get("MODEL_API_BASE", "https://api.meta.ai/v1"),
)

response = client.responses.create(
    model="muse-spark-1.3",
    input="Valio amin'ny teny Malagasy, fehezanteny iray fotsiny: inona no atao hoe 'merci beaucoup'?",
)
print(response.output_text)