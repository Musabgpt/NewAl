---
name: خوادم وواجهات API
description: خادم API صحيح وآمن مع قاعدة بيانات
triggers: api, backend, server, خادم, سيرفر, fastapi, flask, django, express, endpoint, قاعدة بيانات, database, sqlite, sql
---
- Python: FastAPI + uvicorn (typed models with pydantic), or Flask for tiny apps. Node: Express.
- Start with SQLite (no install); use parameterised queries or an ORM, never string-built SQL.
- Validate every input, return proper status codes (400/404/500) with a JSON error message.
- Config and secrets from environment variables / `.env` (never committed).
- A server keeps running: test it with a short script that starts it, calls each endpoint (`requests`/`httpx` or FastAPI `TestClient`) and stops it.
