# Knowledge embeddings (network + local-hash fallback)

AetherChat knowledge RAG uses **FTS5 + BM25** for lexical search and **semantic vectors** for hybrid ranking.

## Modes

| Provider | Meaning |
|----------|---------|
| `openai` / `compat` | OpenAI-compatible `POST {base}/embeddings` (OpenAI, Zhipu/GLM, etc.) |
| `local-hash` | Offline blake2b 256-d feature hash (previous default) |

Default is `openai` (protocol). If no usable API key/base URL is found, the service soft-falls back to `local-hash` so indexing and search never break.

## Environment

```bash
AETHERCHAT_EMBEDDING_PROVIDER=openai|compat|local-hash
AETHERCHAT_EMBEDDING_MODEL=text-embedding-3-small   # or embedding-3 for Zhipu
AETHERCHAT_EMBEDDING_BASE_URL=https://api.openai.com/v1
AETHERCHAT_EMBEDDING_API_KEY=sk-...                 # optional override
```

When env is incomplete, the backend auto-picks from the model vault:

1. OpenAI (`OPENAI_API_KEY` / gpt) → `text-embedding-3-small`
2. Zhipu/GLM (`MODEL_KEY_GLM`, `bigmodel.cn`) → `embedding-3`
3. Otherwise local-hash

## Schema

`knowledge_vectors` stores `vector` plus `dimensions`, `provider`, `model`, `created_at`. Legacy 256-d hash rows are backfilled as `local-hash` / `blake2b-256d`. Search only compares vectors with matching dimensions.

## Reindex

```bash
AETHERCHAT_DATA_DIR=~/.config/aetherchat-frontend/backend-data \
  .venv-linux/bin/python scripts/reindex_knowledge_embeddings.py

# status only
.venv-linux/bin/python scripts/reindex_knowledge_embeddings.py --status-only
```

HTTP (authenticated local API):

- `GET /knowledge/embedding/status`
- `POST /knowledge/reindex`
- `POST /knowledge/{session_id}/reindex`
