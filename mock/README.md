# aiper — mock backend

A separate service that serves the same HTTP contract as `backend/`, with
in-memory state and a scripted agent. It exists so the frontend can be
developed, demoed and reviewed with no Azure key, no Postgres and no Qdrant.

```bash
docker compose -f docker-compose.mock.yml up --build
```

Sign in with **demo@aiper.dev** / **demo1234**, or register a new account.

There is no feature flag and no shared code with the real service — the two
never import each other. You choose one by choosing a compose file.

## What is real, and what is not

| Real | Simulated |
| --- | --- |
| Registration, login, bearer auth, access control | LLM inference — turns are scripted |
| Upload, type and size validation, storage | Embeddings — replaced by TF-IDF style lexical scoring |
| **Page parsing** — real `pypdf` / `python-pptx` / `python-docx`, one page = one chunk | Qdrant — replaced by an in-memory index |
| **Retrieval and page citations** — read from your own files | Compliance verdicts — banded off the retrieval score |
| Documents, revisions, block diffs, restore, sharing | Postgres — replaced by dicts |
| The complete SSE event stream and the UI activity feed | |

The answer text is not intelligent. The page numbers in it are, which makes this
a fair test of ingestion, streaming, the activity feed, the editor import path
and version control.

## Layout

| File | Role |
| --- | --- |
| `app/main.py` | The HTTP surface — the same routes and status codes as `backend/` |
| `app/store.py` | In-memory records, lexical retrieval, the revision graph |
| `app/agent.py` | The scripted agent; emits the exact event shapes `backend/app/agents/runtime.py` produces — skill loads, plans, delegations with nested sub-agent calls, draft staging |
| `app/seed.py` | The demo account and two generated sample PDFs |
| `app/loaders.py`, `app/diff.py`, `app/documents.py`, `app/templates.py`, `app/schemas.py` | Copies of their `backend/` counterparts, so behaviour matches without a shared import |

## Seeded data

- The six built-in templates.
- The demo account above.
- Two sample PDFs, **generated with reportlab at boot** and then parsed back
  through the same page-aware loader the real backend uses:

| File | Role | Contents |
| --- | --- | --- |
| `MIS-REQ-Baseline.pdf` | target | 3 pages of HELIOS-2 mission requirements |
| `Supplier-Datasheet-OptiCam.pdf` | source | 3 pages of payload datasheet |

Their values conflict on purpose — 30 krad(Si) required against 20 krad(Si)
offered, 15 km swath against 12.4 km, 400 Mbit/s against 320 Mbit/s per link — so
a Feature Comparison returns a mixed verdict spread on the first run.

## Pacing

`app/agent.py` paces its events deliberately (~0.4 s per tool call, ~12 ms per
token) so the activity feed and the streaming answer can be judged at the speed
they will actually run.
