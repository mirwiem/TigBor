import os
import json
import base64
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-in-production")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

DATABASE = os.getenv("DATABASE_PATH", "tigbor.db")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6")
ALLOWED_PDF = {"pdf"}

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY")) if os.getenv("OPENAI_API_KEY") else None


def db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS tutor_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        filename TEXT NOT NULL,
        openai_file_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS study_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        subject TEXT NOT NULL,
        minutes INTEGER NOT NULL,
        note TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS study_rooms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        subject TEXT NOT NULL,
        owner_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(owner_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS room_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(room_id) REFERENCES study_rooms(id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    """)
    conn.commit()
    conn.close()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Login required."}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def openai_required():
    if client is None:
        return jsonify({"error": "OPENAI_API_KEY is not configured."}), 503
    return None


def tutor_file_for_user():
    conn = db()
    row = conn.execute(
        "SELECT * FROM tutor_files WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (session["user_id"],)
    ).fetchone()
    conn.close()
    return row


def clean_json(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:-1] if len(lines) >= 2 else lines
        text = "\n".join(lines)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("The AI did not return valid JSON.")


def ai_response(instructions, content, file_id=None):
    payload = {
        "model": OPENAI_MODEL,
        "instructions": instructions,
        "input": content,
    }
    if file_id:
        payload["input"] = [{
            "role": "user",
            "content": [
                {"type": "input_file", "file_id": file_id},
                {"type": "input_text", "text": content if isinstance(content, str) else str(content)}
            ]
        }]
    response = client.responses.create(**payload)
    return response.output_text


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if len(username) < 3 or len(password) < 6:
            flash("Username must be at least 3 characters and password at least 6 characters.")
            return render_template("register.html")
        try:
            conn = db()
            conn.execute(
                "INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)",
                (username, generate_password_hash(password), datetime.utcnow().isoformat())
            )
            conn.commit()
            conn.close()
            flash("Account created. You can now sign in.")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("That username is already taken.")
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        conn = db()
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("dashboard"))
        flash("Invalid username or password.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def dashboard():
    conn = db()
    logs = conn.execute(
        "SELECT * FROM study_logs WHERE user_id=? ORDER BY created_at DESC LIMIT 8",
        (session["user_id"],)
    ).fetchall()
    total = conn.execute(
        "SELECT COALESCE(SUM(minutes),0) AS total FROM study_logs WHERE user_id=?",
        (session["user_id"],)
    ).fetchone()["total"]
    conn.close()
    return render_template("dashboard.html", logs=logs, total=total)


@app.route("/chatbot")
def chatbot():
    return render_template("chatbot.html")


@app.route("/api/chatbot", methods=["POST"])
def api_chatbot():
    error = openai_required()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    history = data.get("history", [])[-12:]
    if not message:
        return jsonify({"error": "Enter a question."}), 400

    instructions = """You are TigBor Chatbot, a general academic and study assistant.
Answer clearly and accurately. Explain concepts when useful. You may answer open-ended
questions, unlike TigBor AI Tutor. Do not claim to know information you do not know.
For calculations, show the important working. Keep answers readable for university students."""
    content = [{"role": "user", "content": message}]
    if history:
        content = history + content

    try:
        text = ai_response(instructions, content)
        return jsonify({"answer": text})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/tutor")
@login_required
def tutor():
    active = tutor_file_for_user()
    return render_template("tutor.html", active=active)


@app.route("/api/tutor/upload", methods=["POST"])
@login_required
def tutor_upload():
    error = openai_required()
    if error:
        return error
    uploaded = request.files.get("pdf")
    if not uploaded or not uploaded.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Please upload a PDF file."}), 400

    filename = secure_filename(uploaded.filename)
    try:
        uploaded.stream.seek(0)
        file_obj = client.files.create(
            file=(filename, uploaded.stream, "application/pdf"),
            purpose="user_data"
        )
        conn = db()
        conn.execute("DELETE FROM tutor_files WHERE user_id=?", (session["user_id"],))
        conn.execute(
            "INSERT INTO tutor_files(user_id,filename,openai_file_id,created_at) VALUES(?,?,?,?)",
            (session["user_id"], filename, file_obj.id, datetime.utcnow().isoformat())
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "filename": filename})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/tutor/chat", methods=["POST"])
@login_required
def tutor_chat():
    error = openai_required()
    if error:
        return error
    active = tutor_file_for_user()
    if not active:
        return jsonify({"error": "Upload a PDF before using AI Tutor."}), 400

    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    history = data.get("history", [])[-10:]
    if not message:
        return jsonify({"error": "Enter a question."}), 400

    instructions = """You are TigBor AI Tutor. You MUST use the uploaded PDF as the
source of truth and you must teach rather than simply dump answers.

Use an attempt-first tutoring method:
1. Identify what the student is asking.
2. If their question is ambiguous, ask a short clarification question.
3. Ask what they already understand or ask them for their first attempt.
4. Give a small hint or guiding question instead of the final answer.
5. If the student provides an attempt, evaluate it and guide them one step further.
6. Reveal the direct answer only after the student has made a meaningful attempt,
or after several helpful guided steps when continuing to withhold it would prevent learning.

Never invent material that is not supported by the PDF. If the PDF does not contain
the requested information, say so clearly. Keep the interaction encouraging and concise."""
    content = history + [{"role": "user", "content": message}]

    try:
        text = ai_response(instructions, content, active["openai_file_id"])
        return jsonify({"answer": text})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/tutor/generate", methods=["POST"])
@login_required
def tutor_generate():
    error = openai_required()
    if error:
        return error
    active = tutor_file_for_user()
    if not active:
        return jsonify({"error": "Upload a PDF first."}), 400

    data = request.get_json(silent=True) or {}
    kind = data.get("kind")
    count = max(1, min(int(data.get("count", 10)), 30))

    prompts = {
        "notes": """Create personalized university-level study notes from the PDF.
Organize them into clear headings, key concepts, important definitions, formulas,
common mistakes, and a short revision checklist. Do not add unsupported facts.""",
        "quiz": f"""Create {count} university-level multiple-choice questions based ONLY
on the PDF. Return valid JSON with this exact shape:
{{"questions":[{{"question":"...","options":["A","B","C","D"],"answer":"A","explanation":"..."}}]}}""",
        "flashcards": f"""Create {count} concise study flashcards based ONLY on the PDF.
Return valid JSON with this exact shape:
{{"cards":[{{"front":"...","back":"..."}}]}}"""
    }

    if kind not in prompts:
        return jsonify({"error": "Unknown generator."}), 400

    try:
        text = ai_response(
            "You are TigBor's PDF study-material generator. " + prompts[kind],
            "Generate the requested material from the uploaded PDF.",
            active["openai_file_id"]
        )
        if kind in {"quiz", "flashcards"}:
            return jsonify({"kind": kind, "data": clean_json(text)})
        return jsonify({"kind": kind, "text": text})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/study")
@login_required
def study():
    return redirect(url_for("dashboard"))


@app.route("/api/study/log", methods=["POST"])
@login_required
def study_log():
    data = request.get_json(silent=True) or {}
    subject = data.get("subject", "").strip()
    minutes = int(data.get("minutes", 0))
    note = data.get("note", "").strip()
    if not subject or minutes <= 0 or minutes > 1440:
        return jsonify({"error": "Provide a subject and valid study duration."}), 400
    conn = db()
    conn.execute(
        "INSERT INTO study_logs(user_id,subject,minutes,note,created_at) VALUES(?,?,?,?,?)",
        (session["user_id"], subject, minutes, note, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/online")
@login_required
def online():
    conn = db()
    rooms = conn.execute("""
        SELECT r.*, u.username AS owner
        FROM study_rooms r JOIN users u ON u.id=r.owner_id
        ORDER BY r.created_at DESC
    """).fetchall()
    conn.close()
    return render_template("online.html", rooms=rooms)


@app.route("/api/rooms", methods=["POST"])
@login_required
def create_room():
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    subject = data.get("subject", "").strip()
    if not name or not subject:
        return jsonify({"error": "Room name and subject are required."}), 400
    conn = db()
    cur = conn.execute(
        "INSERT INTO study_rooms(name,subject,owner_id,created_at) VALUES(?,?,?,?)",
        (name, subject, session["user_id"], datetime.utcnow().isoformat())
    )
    conn.commit()
    room_id = cur.lastrowid
    conn.close()
    return jsonify({"id": room_id})


@app.route("/online/<int:room_id>")
@login_required
def room(room_id):
    conn = db()
    room = conn.execute("SELECT * FROM study_rooms WHERE id=?", (room_id,)).fetchone()
    messages = conn.execute("""
        SELECT m.*, u.username FROM room_messages m
        JOIN users u ON u.id=m.user_id
        WHERE m.room_id=? ORDER BY m.id ASC
    """, (room_id,)).fetchall()
    conn.close()
    if not room:
        return "Room not found", 404
    return render_template("room.html", room=room, messages=messages)


@app.route("/api/rooms/<int:room_id>/messages", methods=["POST"])
@login_required
def send_message(room_id):
    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "Message cannot be empty."}), 400
    conn = db()
    conn.execute(
        "INSERT INTO room_messages(room_id,user_id,message,created_at) VALUES(?,?,?,?)",
        (room_id, session["user_id"], message, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/formulas")
def formulas():
    return render_template("formulas.html")


@app.route("/exam")
@login_required
def exam():
    return render_template("exam.html")


@app.route("/api/exam/generate", methods=["POST"])
@login_required
def exam_generate():
    error = openai_required()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    subject = data.get("subject", "Mixed")
    difficulty = data.get("difficulty", "University")
    count = max(1, min(int(data.get("count", 10)), 30))
    minutes = max(5, min(int(data.get("minutes", 30)), 180))

    prompt = f"""Generate an exam simulation for a university student.
Subject: {subject}
Difficulty: {difficulty}
Number of questions: {count}
Time limit: {minutes} minutes.

Return valid JSON only:
{{
  "title":"...",
  "instructions":"...",
  "questions":[
    {{
      "id":1,
      "question":"...",
      "type":"short_answer",
      "marks":5
    }}
  ]
}}
Make questions exam-like, unambiguous, and suitable for manual written answers.
Do not include the answers in this response."""
    try:
        text = ai_response(
            "You are TigBor Exam Engine. " + prompt,
            "Generate the exam now."
        )
        exam_data = clean_json(text)
        exam_data["minutes"] = minutes
        return jsonify(exam_data)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/exam/check", methods=["POST"])
@login_required
def exam_check():
    error = openai_required()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    question = data.get("question", "")
    answer = data.get("answer", "")
    image = data.get("image")

    if not question:
        return jsonify({"error": "Question is required."}), 400
    if not answer and not image:
        return jsonify({"error": "Provide a typed answer or a photo."}), 400

    content = [{"type": "input_text", "text":
        f"""Check this student's exam answer.

Question:
{question}

Typed answer:
{answer or "[No typed answer; inspect the attached photo.]"} 

Return:
1. Correctness
2. Score suggestion
3. What was done correctly
4. What is missing or wrong
5. A concise model solution

Be fair and do not award credit for unsupported claims."""
    }]
    if image:
        content.append({"type": "input_image", "image_url": image})

    try:
        response = client.responses.create(
            model=OPENAI_MODEL,
            instructions="You are TigBor's exam marking assistant. Carefully inspect text and images.",
            input=[{"role": "user", "content": content}]
        )
        return jsonify({"result": response.output_text})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": "The uploaded file is too large."}), 413


@app.context_processor
def inject_user():
    return {"current_user": session.get("username")}


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
