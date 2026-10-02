# Voice CRM Agent

A voice and text assistant for a small business, like a receptionist that works around the clock. Customers talk or type to it in a web page. It books appointments, opens support tickets, reports on a customer's own bookings, answers questions from the business's own PDF documents, and hands over to a human when asked.

It listens with **Groq Whisper**, thinks with a **LangGraph** agent running on **Groq language models**, and speaks with the **browser's built-in voice** (or ElevenLabs if you add a key). It runs on **Django + PostgreSQL (pgvector) + Redis** and deploys to **AWS** from GitHub with no stored AWS keys.

---

## Table of contents

1. [What it does](#what-it-does)
2. [Example conversations](#example-conversations)
3. [How it works (design)](#how-it-works-design)
4. [Security](#security)
5. [Tech stack](#tech-stack)
6. [Project layout](#project-layout)
7. [API reference](#api-reference)
8. [Configuration](#configuration)
9. [Deploying to AWS](#deploying-to-aws)
10. [Running locally](#running-locally)
11. [Testing and CI/CD](#testing-and-cicd)
12. [Operating it](#operating-it)
13. [Known limitations](#known-limitations)
14. [Troubleshooting](#troubleshooting)

---

## What it does

| Feature | What the customer experiences |
|---|---|
| **Book appointments** | "Book a haircut tomorrow at 10." The assistant asks for any missing detail, checks business hours and free slots, then asks the customer to say **yes** before it saves anything. |
| **Support tickets** | "My internet has been down all day." It summarises the problem, picks a priority (low, normal, high) and creates a ticket after the customer confirms. |
| **Status check** | "What do I have coming up?" It lists the customer's own upcoming appointments and open tickets. |
| **FAQ from your PDFs** | Staff upload PDFs (prices, policies, how-to guides). The assistant answers only from those documents and says so when it cannot find an answer. |
| **Human handoff** | "I want to talk to a person." It opens a high-priority ticket for the team. |
| **Emergency handling** | Words like "chest pain" skip the AI entirely and return a fixed safety message that points to local emergency services. |
| **Voice in and out** | Press **Talk**, speak, and hear the answer. Speech to text uses Groq Whisper. Speech output uses the browser voice, or ElevenLabs when configured. |
| **Accounts and admin** | Login with JWT, self-service sign-up (can be locked down), and the Django admin for the business owner to see appointments, tickets and documents. |
| **Observability** | Liveness and readiness endpoints and Prometheus metrics (token protected). |

### Booking rules

- Bookings are accepted from **09:00 up to (not including) 17:00** in the configured time zone, on the hour or half hour.
- Past times are refused.
- Two customers cannot hold the same slot. If a slot is taken, the assistant offers the next free slot within seven days.
- Nothing is saved until the customer confirms with a yes. A "no" cancels it.

---

## Example conversations

```
You:        Book a haircut tomorrow at 10
Assistant:  Book haircut on Friday 02 October at 10:00 AM? Say yes to confirm.
You:        yes
Assistant:  Done! Your appointment is booked.

You:        My internet has been down all day
Assistant:  I'll open a normal priority ticket: Internet is down. Say yes to confirm.
You:        yes
Assistant:  Done! Your ticket number is 1.

You:        What do I have coming up?
Assistant:  Upcoming appointments: haircut on Friday 02 October at 10:00 AM. Open tickets: number 1, Internet is down.

You:        What are your opening hours?          (after a PDF with the hours was uploaded)
Assistant:  <answer taken from the PDF>

You:        What is the meaning of life?          (not in any PDF)
Assistant:  I couldn't find that in our documents. I can open a support ticket or connect you with a person if you like.

You:        I have chest pain
Assistant:  This sounds urgent. I can't help with emergencies. Please call your local emergency number right now...
```

Greetings such as "hello" are treated as a general question, so with no PDFs uploaded the assistant answers that it could not find anything.

---

## How it works (design)

### Request flow

```
Browser (voice.html)
   | text, or recorded audio
   v
POST /api/voice/            POST /api/agent/chat/
   |  Groq Whisper -> text      |
   +-----------+----------------+
               v
         run_agent()  (length check, metrics, PII-masked logging)
               v
        LangGraph agent  ->  reply  ->  output guard  ->  JSON
               v
Browser shows the reply and speaks it (ElevenLabs or browser voice)
```

### The agent (LangGraph)

```
START -> safety --(emergency words)-----------------------------> guard -> END
            |--(a yes/no answers a waiting confirmation)--> confirm --^
            '--> classify --> book | ticket | status | faq | handoff --> guard
```

| Node | Job |
|---|---|
| `safety` | Runs first. A keyword screen. On a hit it returns the fixed emergency message and never calls an LLM. |
| `classify` | One LLM call that labels the message as book, ticket, status, human or faq. Unknown output defaults to faq. A new request discards any unanswered confirmation. |
| `book` | Extracts service, date and time as JSON, merges with what the customer already said, validates hours and slots, then stores a **pending** action in the cache for 5 minutes. |
| `ticket` | Summarises the problem and picks a priority, then stores a **pending** ticket. |
| `confirm` | Handles "yes" or "no". Yes creates the record. A short lock stops a double "yes" from creating two records. "No" always wins over "yes" so nothing is saved by accident. |
| `status` | Reads the customer's own upcoming appointments and open tickets. |
| `faq` | Grounded question answering over the uploaded PDFs (below). |
| `handoff` | Opens a high-priority ticket asking for a human. |
| `guard` | Last step. Removes markdown symbols (a voice would read them out) and caps the reply at 600 characters. |

All state between turns (pending confirmations, half-filled bookings) lives in the cache (Redis in production), keyed by user.

### Knowledge base (RAG) pipeline

**Ingest (staff upload):**

1. Validate the file: it must start with `%PDF-`, be at most 10 MB and 50 pages, not be encrypted, and contain real text (scans are rejected).
2. Hash the file (SHA-256) so the same PDF cannot be added twice.
3. Split the text into chunks of about 900 characters with 150 characters of overlap, preferring sentence boundaries (at most 400 chunks per document).
4. Embed each chunk locally with **fastembed** (`BAAI/bge-small-en-v1.5`, 384 dimensions). The model is baked into the Docker image, so no model download happens at runtime.
5. Store the chunks and vectors in PostgreSQL with a pgvector **HNSW** index (cosine distance). The PDF file itself is not stored, only its text chunks.

**Answer (every FAQ question):**

1. Embed the question and fetch the top 4 most similar chunks.
2. **Relevance gate:** if no chunk reaches similarity 0.5, stop. No LLM call is made.
3. Ask the LLM to answer from the numbered passages only, in at most three short sentences, or reply `NOT_FOUND`.
4. **Grounding gate:** embed each sentence of the answer and compare it with the retrieved chunks. If the average best similarity is below 0.55, the answer is rejected and the "couldn't find that" reply is returned instead.

This design makes the assistant prefer saying "I don't know" over inventing an answer.

### Language models

`LLM_MODELS` is a comma-separated list tried in order, so one retired or overloaded model does not take the assistant down. The default is:

```
openai/gpt-oss-120b, openai/gpt-oss-20b
```

These are reasoning models, so the code asks for short reasoning (`reasoning_effort: low`) and leaves extra room in the token budget. Groq retired its earlier Llama models in August 2026, so model names do change. Override them with the `LLM_MODELS` environment variable. Speech to text uses `whisper-large-v3-turbo` (`STT_MODEL`).

### Data model

| Model | Fields | Notes |
|---|---|---|
| `Appointment` | customer, service, starts_at, status (booked/cancelled), created_at | A database constraint allows only one **booked** appointment per `starts_at`. |
| `Ticket` | customer, summary, priority (low/normal/high), status (open/closed), created_at | |
| `Document` | title, filename, sha256 (unique), pages, chunk_count, uploaded_by | |
| `Chunk` | document, position, text, embedding (vector 384) | HNSW index on the embedding. |

### Deployment architecture (AWS)

```
git push -> GitHub Actions: test -> build Docker image -> push to ECR -> roll out on ECS

Variant A (HTTPS):  Browser -> CloudFront (*.cloudfront.net) -> ALB -> ECS Fargate (Django, gunicorn)
Variant B (HTTP):   Browser -> ALB -> ECS Fargate (Django, gunicorn)

ECS Fargate -> RDS PostgreSQL 16 (pgvector)   (private)
            -> ElastiCache Redis              (private)
            -> Secrets Manager                (keys and passwords)
            -> CloudWatch Logs                (/ecs/voice-crm)
```

Everything is created from one CloudFormation template (`infra/cloudformation.yml`, or `infra/cloudformation-nocf.yml` without CloudFront). The container runs `migrate` and creates the first admin on every start, then serves with gunicorn (one worker, eight threads, so the embedding model loads once).

---

## Security

### Secrets and configuration
- No secrets are in the repository. Everything secret comes from environment variables. On AWS they are held in **Secrets Manager** and injected into the container at start. `.env` is excluded by `.gitignore`.
- `DJANGO_SECRET_KEY` is required whenever `DEBUG` is off (the app refuses to start without it). On AWS it is generated automatically.
- The database master password is created and managed by RDS in Secrets Manager.
- Never paste real keys or passwords into chats, issues, commits or logs. If a key leaks, delete it at the provider and create a new one.

### Authentication and authorization
- JWT login (access token 2 hours, refresh token 1 day). All API endpoints require authentication by default. The browser keeps the token in memory only, not in `localStorage`.
- Customers only ever see their own appointments and tickets.
- Uploading, listing and deleting documents is **staff only**. The Django admin is for staff and superusers.
- Password rules: at least 10 characters, not a common password, not all numeric, not too similar to the username.
- Registration can be switched off (`REGISTRATION_ENABLED=false`) or protected by an invite code (`REGISTRATION_INVITE_CODE`, compared in constant time). Usernames are validated (3 to 30 letters, numbers, `. _ -`).

### Abuse protection
- **Rate limiting** per client IP, using atomic counters in Redis:

  | Endpoint | Limit |
  |---|---|
  | `/api/auth/register/` | 5 per hour |
  | `/api/token/` (login) | 10 per minute |
  | `/api/voice/speak/` | 30 per minute |
  | `/api/voice/` | 20 per minute |
  | `/api/documents/` | 10 per minute |
  | other `/api/` | 60 per minute |

  Exceeding a limit returns HTTP 429. If Redis is unreachable the limiter lets requests through and logs the error (it fails open on purpose so an outage does not lock everyone out).
- **Spoof-resistant client IP:** the IP is read from the trusted end of `X-Forwarded-For` according to `TRUSTED_PROXY_COUNT`, so a client cannot fake its address by sending its own header.
- **Input sanitizing:** JSON request bodies have control characters and HTML tags removed, and are capped at 64 KB. Password fields are never altered.
- Chat messages are limited to 1000 characters.

### File and audio handling
- **PDFs:** magic-byte check, 10 MB and 50 page limits, encrypted files rejected, duplicate detection by SHA-256, filenames sanitized, only extracted text is stored.
- **Audio:** only an allowlist of content types is accepted, 5 MB maximum, and the client's file name is never trusted (the extension comes from the content type).

### AI safety
- **Emergencies bypass the AI** entirely (keyword list in `agent_core/nodes.py`; review it for your business and region).
- **Confirm before writing:** appointments and tickets are only created after an explicit yes. A lock prevents double submissions, and a database constraint prevents double booking even under concurrent requests.
- **Grounded answers:** relevance and grounding gates (see above) reduce made-up answers.
- **Prompt-injection awareness:** the system prompt tells the model that document passages are reference data, never instructions.
- **Output guard:** markdown stripped and length capped before a reply is shown or spoken.
- **Privacy in logs:** emails and phone numbers are masked in log lines, and only the first 80 characters of a message are logged.

### Web hardening
- CSRF protection, `X-Frame-Options: DENY`, `nosniff`, and a `same-origin` referrer policy.
- When served over HTTPS (the CloudFront variant): secure cookies and HSTS for one year.
- Error responses are generic. Details go to the logs, not to the user.

### Container and cloud
- The container runs as a **non-root user** (uid 10001) with read-only application files. Secrets are never baked into the image.
- **RDS and Redis are not public.** Their security groups accept connections only from the app containers, and the containers accept traffic only from the load balancer. The database storage is encrypted.
- In the CloudFront variant, the load balancer rejects any request that does not carry a secret header added by CloudFront, so the load balancer cannot be used directly.
- The metrics endpoint is disabled unless `METRICS_TOKEN` is set, and then needs the `X-Metrics-Token` header (constant-time comparison).
- The `/health/` check answers before host validation and does not touch the database.

### CI/CD security
- **No AWS keys anywhere.** GitHub Actions proves its identity to AWS with **OIDC** and assumes a role.
- The role trusts only this repository's `main` branch, and it can only push to this ECR repository and update this one ECS service.
- Note: GitHub repositories created after 15 July 2026 use an immutable subject claim of the form `repo:OWNER@OWNER_ID/REPO@REPO_ID:ref:refs/heads/main`. The role's trust policy must match that format (the templates in this project accept both formats).

### What is not covered (be honest with yourself)
- **Variant B (no CloudFront) is plain HTTP.** Passwords and tokens travel unencrypted, and browsers block the microphone. Use it only for testing, or add HTTPS before real use.
- Sign-up is open by default. Restrict it before sharing the link.
- The emergency check is a keyword list, not a medical classifier.
- Documents are shared by all users (one knowledge base).
- There is no WAF, no per-user rate limiting (limits are per IP), and no audit log beyond the application logs.

---

## Tech stack

| Area | Technology |
|---|---|
| Backend | Django 6, Django REST Framework, SimpleJWT |
| Agent | LangGraph |
| LLM and speech to text | Groq (`openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `whisper-large-v3-turbo`) |
| Embeddings | fastembed, `BAAI/bge-small-en-v1.5` (384 dimensions), runs locally |
| Database | PostgreSQL 16 with pgvector (HNSW, cosine) |
| Cache and rate limits | Redis |
| PDF parsing | pypdf |
| Text to speech | Browser SpeechSynthesis (free), ElevenLabs (optional) |
| Serving | gunicorn, WhiteNoise |
| Metrics | prometheus_client |
| Infrastructure | AWS CloudFormation: ECS Fargate, ALB, CloudFront (optional), RDS, ElastiCache, ECR, Secrets Manager, CloudWatch |
| CI/CD | GitHub Actions with OIDC |

---

## Project layout

```
config/          settings.py (all env-driven), urls.py, wsgi.py
security/        health check, atomic rate limiter, proxy-aware client IP, JSON input sanitizer
mlops/           Prometheus metrics, /health/ready/ and /health/metrics/
rag_core/        llm.py (Groq with model fallbacks), embeddings.py, retrieval.py (pgvector), answer.py (grounding)
agent_core/      nodes.py, graph.py (LangGraph), guards.py, service.py
apps/accounts    register, me, ensure_admin command
apps/crm         Appointment, Ticket (+ admin)
apps/documents   PDF upload, validation, chunking, embedding (staff only)
apps/agent       POST /api/agent/chat/
apps/voice       POST /api/voice/ (speech to text + agent), POST /api/voice/speak/ (ElevenLabs)
templates/       voice.html (login, chat, microphone, staff upload)
tests/           79 tests; Groq, embeddings and ElevenLabs are mocked
infra/           cloudformation.yml (HTTPS via CloudFront), cloudformation-nocf.yml (plain HTTP)
.github/         ci.yml (tests + image build check), deploy.yml (build, push to ECR, roll out)
```

---

## API reference

All `/api/` endpoints need `Authorization: Bearer <access token>` unless noted.

| Method and path | Purpose | Access |
|---|---|---|
| `POST /api/auth/register/` | Create an account (`username`, `password`, optional `email`, `invite_code`) | Public |
| `POST /api/token/` | Log in, returns `access` and `refresh` | Public |
| `POST /api/token/refresh/` | Get a new access token | Public |
| `GET /api/auth/me/` | Current username and `is_staff` | User |
| `POST /api/agent/chat/` | Send `{"text": "..."}`, get `{"reply", "intent"}` | User |
| `POST /api/voice/` | Upload audio (`audio` field), get `transcript`, `reply`, `intent` | User |
| `POST /api/voice/speak/` | Text to MP3 via ElevenLabs (returns 501 if not configured) | User |
| `GET/POST /api/documents/` | List or upload PDFs (`file`, optional `title`) | Staff |
| `DELETE /api/documents/<id>/` | Remove a document and its chunks | Staff |
| `GET /health/` | Liveness (no database check) | Public |
| `GET /health/ready/` | Readiness (database and cache) | Public |
| `GET /health/metrics/` | Prometheus metrics, header `X-Metrics-Token` | Token |
| `/admin/` | Django admin | Staff |
| `/` and `/voice/` | The web page | Public |

Metrics exposed: `agent_requests_total` (by intent), `agent_errors_total`, `agent_total_seconds`, `voice_stt_seconds`, `rag_grounding_score`, `rag_refusals_total` (by reason).

---

## Configuration

On AWS these come from CloudFormation and Secrets Manager. For local Docker, copy `.env.example` to `.env`.

| Variable | Default | Purpose |
|---|---|---|
| `DEBUG` | `false` | Never `true` in production |
| `DJANGO_SECRET_KEY` | none (required when not DEBUG) | Django signing key |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Allowed host names |
| `CSRF_TRUSTED_ORIGINS` | empty | Origins allowed for CSRF (include `http://` or `https://`) |
| `DATABASE_URL` or `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | local Postgres | Database |
| `REDIS_URL` | empty (in-memory cache) | Cache, rate limits, pending confirmations |
| `GROQ_API_KEY` | none | Groq access |
| `LLM_MODELS` | `openai/gpt-oss-120b,openai/gpt-oss-20b` | Models tried in order |
| `STT_MODEL` | `whisper-large-v3-turbo` | Speech to text model |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL` | empty | Optional voice; empty means the free browser voice |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | none | First superuser, created on first start if missing |
| `TIME_ZONE` | `UTC` | Business time zone (booking hours use it) |
| `REGISTRATION_ENABLED` | `true` | Turn sign-up off |
| `REGISTRATION_INVITE_CODE` | empty | Require a code to sign up |
| `METRICS_TOKEN` | empty | Enables `/health/metrics/` |
| `BEHIND_CLOUDFRONT` | `true` unless DEBUG | Secure cookies, HSTS, HTTPS header handling |
| `TRUSTED_PROXY_COUNT` | 2 (CloudFront + ALB), 1 without CloudFront | Proxies to trust for the client IP |
| `RETRIEVAL_TOP_K`, `RETRIEVAL_MIN_SIM`, `GROUNDING_MIN` | 4, 0.5, 0.55 | RAG tuning |
| `MAX_PDF_BYTES`, `MAX_PDF_PAGES`, `MAX_CHUNKS_PER_DOC` | 10 MB, 50, 400 | Upload limits |
| `LOG_LEVEL` | `INFO` | Logging |
| `RUN_MIGRATIONS` | `1` | Run `migrate` and `ensure_admin` on container start |

Business hours (9 to 17) and the 30-minute slot size are constants in `config/settings.py`.

**Admin password note:** the first admin is created only if it does not exist. Changing `ADMIN_PASSWORD` later does **not** change an existing user. Change it inside `/admin/` (Change password).

---

## Deploying to AWS

You do not need Docker or the AWS CLI on your computer. GitHub builds the image. The AWS CLI is only a convenient way to create the stack (the AWS console works too).

### 1. GitHub
Create a **private** repository and push the code:

```powershell
git init
git add -A
git commit -m "Voice CRM agent"
git branch -M main
git remote add origin https://github.com/YOUR_USER/voice-crm.git
git push -u origin main
```
The first deploy run will fail until step 3 is done. That is expected.

### 2. Create the stack
Use `infra/cloudformation.yml` (HTTPS through CloudFront). If your new AWS account is not yet allowed to create CloudFront distributions, use `infra/cloudformation-nocf.yml` (plain HTTP, microphone blocked by browsers).

| Parameter | Value |
|---|---|
| `AppName` | `voice-crm` |
| `VpcId`, `SubnetIds` | Default VPC and **2 or more public subnets** in different Availability Zones |
| `GitHubRepo` | `YOUR_USER/voice-crm` |
| `CreateGitHubOidcProvider` | `true`, or `false` if IAM already lists `token.actions.githubusercontent.com` |
| `GroqApiKey` | A new Groq key |
| `AdminUsername`, `AdminPassword` | 12+ characters, no quotes or backslashes |
| `TimeZone` | For example `Asia/Karachi` |
| `DesiredCount` | `0` for the first create |

Wait for `CREATE_COMPLETE` (about 10 to 15 minutes), then read the **Outputs**: `GitHubRoleArn`, `Region`, `AppUrl`.

### 3. Connect GitHub to AWS
GitHub repository, **Settings, Secrets and variables, Actions, Variables** tab. Add:
- `AWS_ROLE_ARN` = the `GitHubRoleArn` output
- `AWS_REGION` = the `Region` output (for example `us-east-1`)

### 4. Deploy
**Actions, Deploy to AWS, Run workflow.** It tests, builds the image, pushes it to ECR and starts the service. Then update the stack once and set `DesiredCount` to `1`, changing nothing else. Open `AppUrl`, sign in, upload a PDF, and try the example conversations.

Every later `git push` to `main` tests and deploys automatically.

### Microphone without HTTPS (testing only)
Browsers only allow the microphone on HTTPS or `localhost`. For testing the plain-HTTP variant on your own computer in Chrome or Edge, open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`, add your site's exact `http://` address, set it to **Enabled** and relaunch. This only affects that browser.

### Cost and cleanup
The load balancer, database, Redis and container are billed while the stack exists. To stop paying, delete the stack in CloudFormation (the database takes a final snapshot first, which you can delete separately).

---

## Running locally

Not needed for deployment.

1. Install Docker Desktop.
2. Copy `.env.example` to `.env` and set at least `GROQ_API_KEY`, `DJANGO_SECRET_KEY`, `ADMIN_PASSWORD` and `TIME_ZONE`.
3. `docker compose up --build`
4. Open http://localhost:8000 (the microphone works on `localhost`).

To run the tests without Docker you need PostgreSQL with the pgvector extension:

```powershell
$env:DEBUG="true"; $env:DATABASE_URL="postgres://user:pass@localhost:5432/voice"
python manage.py test tests
```

---

## Testing and CI/CD

- **79 tests** cover accounts, the agent, documents, security and voice. Groq, embeddings and ElevenLabs are mocked, so tests need only PostgreSQL with pgvector.
- `ci.yml` runs `manage.py check`, `makemigrations --check` (fails if a migration was forgotten), the test suite, and a Docker build check.
- `deploy.yml` runs the tests first, then builds the image, pushes it to ECR (`latest` and the commit SHA), and forces a new ECS deployment.

---

## Operating it

| Need | How |
|---|---|
| Logs | CloudWatch log group `/ecs/voice-crm` (look for `agent user=` and `LLM model ... failed`) |
| See bookings and tickets | `https://<your-address>/admin/` |
| Metrics | `GET /health/metrics/` with `X-Metrics-Token` (set `MetricsToken` in the stack) |
| Change keys, time zone or sizes | Update the CloudFormation parameters, then restart the service (**Run workflow**, or force a new ECS deployment) |
| Restrict sign-up | Set `REGISTRATION_INVITE_CODE` or `REGISTRATION_ENABLED=false` as a task environment variable |
| FAQ too strict or too loose | Lower or raise `RETRIEVAL_MIN_SIM` and `GROUNDING_MIN` slightly; watch `rag_grounding_score` and `rag_refusals_total` |

Notes:
- Keep exactly one running task (`DesiredCount` 1), because the container runs migrations on start.
- The embedding model has 384 dimensions. Changing `EMBEDDING_MODEL` to a different size needs a new migration.

---

## Known limitations

- Voice needs HTTPS. Use the CloudFront template (after AWS enables CloudFront for your account) or your own domain and certificate.
- One shared knowledge base. Only text PDFs (no scans), up to 10 MB and 50 pages.
- One business calendar: slots are global, not per staff member or per service.
- The emergency screen is a keyword list.
- English-language embeddings (`bge-small-en`). Other languages need a multilingual embedding model and a migration.
- Rate limits are per IP, not per user.

---

## Troubleshooting

| Problem | Cause and fix |
|---|---|
| "The assistant is busy right now" | All Groq models failed. Check logs: `aws logs tail /ecs/voice-crm --since 20m --filter-pattern "LLM"`. `NotFoundError` means a model name was retired, so update `LLM_MODELS`. `AuthenticationError` means a bad Groq key. |
| "I couldn't find that in our documents" | No PDF uploaded, or the question is not covered (greetings count as questions). Upload a PDF and ask something it contains. |
| Microphone blocked | The page is not HTTPS. See the testing flag above, or use HTTPS. |
| Deploy fails: "Not authorized to perform sts:AssumeRoleWithWebIdentity" | The role's trust policy does not match GitHub's token. For repos created after 15 July 2026 the subject includes IDs (`repo:OWNER@ID/REPO@ID:ref:refs/heads/main`). |
| Stack fails: CloudFront "account must be verified" | New AWS accounts must be approved for CloudFront through AWS Support. Meanwhile use `cloudformation-nocf.yml`. |
| Stack fails: RDS "backup retention exceeds the maximum available to free tier" | Free-plan accounts allow a shorter backup retention. Set `BackupRetentionPeriod` to 1 in the template. |
| Stack stuck in `ROLLBACK_FAILED` | A resource could not be deleted during rollback. Delete the stack again once the database is `available`, or retain the stuck resource and delete it by hand. |
| `OIDC provider already exists` | Recreate the stack with `CreateGitHubOidcProvider=false`. |
| 502 for the first minutes after deploy | The container is still starting. Wait 2 to 3 minutes and refresh. |
| Changed `ADMIN_PASSWORD` but login still uses the old one | The admin user already exists. Change the password in `/admin/`. |
| `$REGION` is empty in PowerShell | Variables vanish when the terminal is reopened. Set `$REGION = "us-east-1"` again. |
