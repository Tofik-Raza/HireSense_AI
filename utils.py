import os, json, httpx, tempfile
os.environ["HF_HUB_DISABLE_SYMLINKS"] = "1"
from faster_whisper import WhisperModel
from dotenv import load_dotenv
load_dotenv()

TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN")
model = "mistral:latest"

OLLAMA_API_URL = "http://localhost:11434/api/generate"

async def llm_resp(prompt: str):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            OLLAMA_API_URL,
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=3000
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "").strip()

async def llm_generate_questions(jd_text: str, count: int = 3):
    prompt = f"""
You are an AI interview question generator.Return the result strictly as a JSON object with keys Q1, Q2, ..., Q{count}, each mapping to a string question.  
Do not include any extra text, explanation, or formatting outside of the JSON.  
JD:
{jd_text}

Generate exactly {count} technical interview questions based on the JD.  

Return the result strictly as a JSON object with keys Q1, Q2, ..., Q{count}, each mapping to a string question.  
Do not include any extra text, explanation, or formatting outside of the JSON.
"""

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            OLLAMA_API_URL,
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=3000
        )
        resp.raise_for_status()
        data = resp.json()
        txt = data.get("response", "").strip()

    try:
        parsed = json.loads(txt)
        return list(parsed.values()) 
    except Exception:
        return [line.strip("- ") for line in txt.splitlines() if line][:count]

# Load model once (you can choose "tiny", "base", "small", "medium", "large-v2")
whisper_model = WhisperModel("base", device="cpu", compute_type="int8")

async def stt_transcribe(recording_url: str):
    async with httpx.AsyncClient() as client:
        audio = await client.get(
            recording_url + ".mp3",
            auth=(TWILIO_SID, TWILIO_TOKEN)
        )
        audio.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio.content)
            fname = f.name

    segments, info = whisper_model.transcribe(fname, language="en")
    text = " ".join(segment.text for segment in segments)
    return text

async def score_answer(jd_text: str, question: str, transcript: str) -> float:
    prompt = f"""
Given JD, Question, and Answer, rate the answer 0–100 strictly.
JD: {jd_text}
Q: {question}
A: {transcript}

Return only valid JSON: {{"score": number}}
"""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            OLLAMA_API_URL,
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=3000
        )
        resp.raise_for_status()
        data = resp.json()
        txt = data.get("response", "").strip()

    try:
        parsed = json.loads(txt)
        return float(parsed.get("score", 0))
    except Exception:
        return 0.0