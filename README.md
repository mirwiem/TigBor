# TigBor

A revamped AI-powered university study platform.

## Stack
- Flask
- SQLite
- OpenAI Responses API
- HTML/CSS/vanilla JavaScript
- Render + Gunicorn

## Local setup (Windows / VS Code)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Put your OpenAI API key in `.env`, then:

```powershell
python app.py
```

Open http://127.0.0.1:5000

## Render
Create a Render Web Service from this repository.
Build command:
`pip install -r requirements.txt`

Start command:
`gunicorn app:app`

Set `OPENAI_API_KEY` in Render Environment Variables. Do not commit `.env`.
The included `render.yaml` can also be used as a Blueprint.

## Important MVP notes
- The PDF Tutor stores the current OpenAI file ID for each user.
- Generator tools are only available after a PDF upload.
- Exam Mode locks the browser controls after submission/time expiry; a future production version should add server-side exam-session enforcement and stronger anti-cheat controls.
- SQLite is suitable for a prototype. For a public multi-user launch, move to Render Postgres and object storage for durable production data.
