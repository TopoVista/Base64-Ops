# LangGraph DevOps Agent API

This backend replaces the previous Node/Express AI server with FastAPI, LangGraph, LangChain, MongoDB, GitHub OAuth, and approval-gated DevOps operations.

## Local setup

```bash
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

Set `MONGO_URI`, GitHub OAuth credentials, `JWT_SECRET`, and optionally `OPENAI_API_KEY`.

## RAG

- Repo code, Docker files, CI config, docs, and runbooks are indexed into `rag_chunks`.
- With `OPENAI_API_KEY`, indexing uses MongoDB Atlas Vector Search through `langchain-mongodb`.
- Without `OPENAI_API_KEY`, the API falls back to keyword retrieval so local development still works.

## DevOps safety

- Read-only diagnostics can run immediately.
- Mutating actions pause through LangGraph human-in-the-loop approval before execution.
