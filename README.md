# Base64 Ops — LangGraph DevOps Coding Agent

Base64 Ops is a full-stack AI coding and DevOps agent. The project now uses a Python FastAPI backend with LangChain, LangGraph, MongoDB, MongoDB Atlas Vector Search, GitHub OAuth, and a redesigned React command-center UI.

## What Changed

- **Backend:** FastAPI replaces the previous Node/Express AI orchestration layer.
- **Agent:** LangGraph coordinates context loading, RAG retrieval, planning, approval gates, tool execution, and final responses.
- **RAG:** repository files, docs, CI/CD config, Docker files, and runbooks can be indexed into MongoDB Atlas Vector Search.
- **DevOps:** GitHub + Docker diagnostics are available, while mutating operations are approval-gated.
- **Frontend:** Vite React remains, but the UI is rebuilt as a dark Ops Command Center with chat, sources, timeline, approvals, and DevOps context.

## Tech Stack

- FastAPI, LangGraph, LangChain, LangChain OpenAI
- MongoDB, Motor, MongoDB Atlas Vector Search
- GitHub OAuth and GitHub REST API
- Docker-backed workspace support
- React, Vite, Tailwind CSS, shadcn/ui, AI Elements-style markdown rendering

## Local Development

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd client
npm install
npm run dev
```

`client/.env.example` points the UI at `http://localhost:8000/api/`.

## Required Environment

- `MONGO_URI`
- `MONGO_DB_NAME`
- `JWT_SECRET`
- `GITHUB_CLIENT_ID`
- `GITHUB_CLIENT_SECRET`
- `GITHUB_OAUTH_STATE_SECRET`
- `GITHUB_TOKEN_ENCRYPTION_KEY`
- `OPENAI_API_KEY` for full LLM + vector RAG behavior
- `CHAT_MODEL=gpt-5.4-mini`
- `EMBEDDING_MODEL=text-embedding-3-small`

## DevOps Safety Model

Read-only diagnostics such as git status, Docker build checks, compose validation, source retrieval, and log-style inspection can run directly. Risky actions such as writes, commits, pushes, workflow dispatches, PR creation, deploys, or rollbacks must first create an approval request in the UI.

## Docker

```bash
docker compose up --build
```

For production-grade RAG, use MongoDB Atlas Vector Search. The bundled Mongo service is useful for local persistence but does not replace Atlas vector search configuration.
