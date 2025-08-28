import os, io, asyncio
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Request, Query
from fastapi.responses import Response, FileResponse
from twilio.twiml.voice_response import VoiceResponse
from twilio.rest import Client
from sqlmodel import SQLModel, Session, select
import pdfplumber
from docx import Document
import json

from models import Candidate, Interview, Question, Answer
from deps import get_session, require_whitelisted, to_e164, engine
from utils import llm_generate_questions, llm_resp, stt_transcribe, score_answer

SQLModel.metadata.create_all(engine, checkfirst=True)

app = FastAPI(title="AI Interview Screener")

async def llm_extract_resume_data(text: str):
    prompt = f"""
Rules:
- Do not include explanations, extra text, or keys that are not listed.
- If any field is missing, return its value as null.
- Work Experience must be a number (float or integer) representing total years (e.g., 1.5, 3, 7).

Return ONLY JSON in the format below:
{{
"Full Name": "extracted name",
  "Email": "extracted email",
  "Phone Number": "extracted number",
}}

Resume Text:
{text[:3000]}
"""
    response = await llm_resp(prompt)

    try:
        return json.loads(response)
    except:
        return {"raw": response, "summary": text[:500]}
def build_results(interview_id: str, session: Session):
    itv = session.get(Interview, interview_id)
    candidate = session.get(Candidate, itv.candidate_id)
    qs = session.exec(
        select(Question).where(Question.interview_id == interview_id)
    ).all()

    ans_all = session.exec(
        select(Answer).where(Answer.interview_id == interview_id)
    ).all()

    answers_by_idx = {}
    for a in ans_all:
        if (a.idx not in answers_by_idx) or (answers_by_idx[a.idx].pending and not a.pending):
            answers_by_idx[a.idx] = a

    scores = [a.score for a in answers_by_idx.values() if a.score is not None]
    overall = sum(scores) / max(1, len(qs))
    itv.overall_score = overall
    itv.recommendation = "proceed" if overall > 0.75 else "consider" if overall > 0.5 else "reject"
    session.add(itv); session.commit()

    return {
        "candidate": candidate,
        "overall_score": overall,
        "recommendation": itv.recommendation,
        "questions": qs,
        "answers": list(answers_by_idx.values()),
    }

@app.get("/")
async def root():
    return FileResponse("index.html")
@app.post("/interview/start")
async def start_interview(
    jd_file: UploadFile = File(...),
    resume_file: UploadFile = File(...),
    session: Session = Depends(get_session)
):
    raw_resume = await resume_file.read()
    text = ""
    if resume_file.filename.lower().endswith(".pdf"):
        with pdfplumber.open(io.BytesIO(raw_resume)) as pdf:
            for p in pdf.pages:
                text += p.extract_text() or ""
    else:
        doc = Document(io.BytesIO(raw_resume))
        text = "\n".join(p.text for p in doc.paragraphs)

    parsed_data = await llm_extract_resume_data(text)
    name = parsed_data.get("Full Name")
    email = parsed_data.get("Email")
    phone = parsed_data.get("Phone Number")

    if not name or not phone:
        raise HTTPException(400, "Resume parsing failed (name/phone missing)")
    phone_e164 = phone
    phone_e164 = to_e164(phone)
    require_whitelisted(phone_e164)
    candidate = Candidate(name=name, phone_e164=phone_e164, email=email)
    session.add(candidate); session.commit()
    jd_text = (await jd_file.read()).decode(errors="ignore")
    if not jd_text.strip():
        raise HTTPException(400, "JD text empty")
    questions = await llm_generate_questions(jd_text, count=3)

    interview = Interview(candidate_id=candidate.id, status="calling", started_at=datetime.utcnow())
    session.add(interview);session.commit()

    if isinstance(questions, dict):
        questions_list = list(questions.values())
    else:
        questions_list = questions

    for i, q in enumerate(questions_list, start=1):
        session.add(Question(interview_id=interview.id, idx=i, text=q))
    session.commit()

    client = Client(os.getenv("TWILIO_SID"), os.getenv("TWILIO_TOKEN"))
    call = client.calls.create(
        to=phone_e164,
        from_=to_e164(os.getenv("TWILIO_PHONE_NUMBER")),
        url=f"{os.getenv('PUBLIC_BASE_URL')}/webhooks/voice/answer?interview_id={interview.id}&i=1",
    )

    return {
        "interview_id": interview.id,
        "candidate": {"name": name, "email": email, "phone": phone_e164},
        "status": "calling",
        "twilio_sid": call.sid
    }

@app.post("/webhooks/voice/answer")
async def voice_answer(
    interview_id: str = Query(...),
    i: int = Query(...),
    session: Session = Depends(get_session)
):
    q = session.exec(
        select(Question).where(
            Question.interview_id == interview_id,
            Question.idx == i
        )
    ).first()
    response = VoiceResponse()

    if not q:
        response.say('No question available.')
        response.hangup()
        return Response(content=str(response), media_type="application/xml")

    response.say('Welcome to the AI interview. Please answer each question after the beep.')
    response.say(f"Question {i}. {q.text}")
    response.record(
        maxLength="90",
        playBeep=True,
        action=f"/webhooks/voice/next?interview_id={interview_id}&i={i}",
        recordingStatusCallback=f"/webhooks/voice/recording-complete?interview_id={interview_id}&i={i}"
    )
    return Response(content=str(response), media_type="application/xml")


@app.post("/webhooks/voice/next")
async def voice_next(
    interview_id: str = Query(...), i: int = Query(...), session: Session = Depends(get_session)
):    
    nxt = i + 1
    q = session.exec(select(Question).where(Question.interview_id == interview_id, Question.idx == nxt)).first()
    response = VoiceResponse()
    if not q:
        itv = session.get(Interview, interview_id)
        itv.status = "completed"
        itv.completed_at = datetime.utcnow()
        session.add(itv); session.commit()
        response.say("Interview completed. Thank you!")
        response.hangup()
        return Response(content=str(response), media_type="application/xml")
    response.say(f"Next question. {q.text}")
    response.record(
        maxLength="90",
        playBeep=True,
        action=f"/webhooks/voice/next?interview_id={interview_id}&i={nxt}",
        recordingStatusCallback=f"/webhooks/voice/recording-complete?interview_id={interview_id}&i={nxt}"
    )
    return Response(content=str(response), media_type="application/xml")

@app.post("/webhooks/voice/recording-complete")
async def recording_complete(
    request: Request,
    interview_id: str = Query(...),
    i: int = Query(...),
    session: Session = Depends(get_session)
):
    form = await request.form()
    rec_url = form.get("RecordingUrl")

    q = session.exec(
        select(Question).where(
            Question.interview_id == interview_id,
            Question.idx == i
        )
    ).first()

    if not q:
        return {"error": "Question not found"}

    a = session.exec(
        select(Answer).where(Answer.interview_id == interview_id, Answer.idx == i)
    ).first()
    if not a:
        a = Answer(
            interview_id=interview_id,
            question_id=q.id,
            idx=i,
            recording_url=rec_url,
            pending=True
        )
        session.add(a)
    else:
        a.recording_url = rec_url or a.recording_url
        a.pending = True
    session.commit()
    answer_id = a.id

    async def process(answer_id: str, interview_id: str, question_text: str, rec_url: str, i: int):
        tx = await stt_transcribe(rec_url)
        sc = await score_answer("JD context", question_text, tx)

        with Session(engine) as bg:
            ans = bg.get(Answer, answer_id)
            ans.transcript = tx
            ans.score = sc
            ans.pending = False
            bg.add(ans); bg.commit()
            total_qs = len(bg.exec(select(Question).where(Question.interview_id == interview_id)).all())
            done = len(bg.exec(select(Answer).where(Answer.interview_id == interview_id, Answer.pending == False)).all())
            if done < total_qs:
                return
            itv = bg.get(Interview, interview_id)
            if itv.status == "notified":
                return 
            itv.status = "notified"
            itv.completed_at = itv.completed_at or datetime.utcnow()
            bg.add(itv); bg.commit()

            data = build_results(interview_id, bg)
            candidate = data["candidate"]

            lines = [f"Final Result Score: {data['overall_score']:.2f}"]
            ans_all = bg.exec(select(Answer).where(Answer.interview_id == interview_id)).all()
            by_idx = {}
            for a2 in ans_all:
                if (a2.idx not in by_idx) or (by_idx[a2.idx].pending and not a2.pending):
                    by_idx[a2.idx] = a2

            for q in data["questions"]:
                a2 = by_idx.get(q.idx)
                lines.append(f"\nQ{q.idx}: {q.text}")
                if a2:
                    lines.append(f"A{q.idx}: {a2.transcript or 'No transcript'}")
                    lines.append(f"Score: {a2.score if a2.score is not None else 'N/A'}")
                else:
                    lines.append(f"A{q.idx}: No transcript")
                    lines.append("Score: N/A")

            sms_body = "\n".join(lines)

            tw = Client(os.getenv("TWILIO_SID"), os.getenv("TWILIO_TOKEN"))
            tw.messages.create(
                to=candidate.phone_e164,
                from_=to_e164(os.getenv("TWILIO_PHONE_NUMBER")),
                body=sms_body
            )
            print("✅ Final SMS sent to candidate!")

    asyncio.create_task(process(answer_id, interview_id, q.text, rec_url, i))
    return {"ok": True}

@app.get("/interviews/{interview_id}/results")
def get_results(interview_id: str, session: Session = Depends(get_session)):
    data = build_results(interview_id, session)
    if not data["answers"]:
        raise HTTPException(status_code=404, detail="No result")

    if all(a.pending for a in data["answers"]):
        raise HTTPException(status_code=404, detail="No result")
    return {
        "candidate": {
            "name": data["candidate"].name,
            "email": data["candidate"].email,
            "phone": data["candidate"].phone_e164
        },
        "overall_score": data["overall_score"],
        "recommendation": data["recommendation"],
        "questions": [{"index": q.idx, "text": q.text} for q in data["questions"]],
        "answers": [{"index": a.idx, "url": a.recording_url,
                     "transcript": a.transcript, "score": a.score} for a in data["answers"]]
    }

