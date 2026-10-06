# Matrix support bot

See the repository README for a clean deployment. `bot.py` runs the Matrix client; the Dockerfile default command runs the optional FastAPI backend. Roles are defined in `agents/legal.md` and `agents/finance.md`. Runtime room IDs and SQLite handoff assignments are generated locally and excluded from Git. Local evidence is loaded from `/app/offline_knowledge`.
