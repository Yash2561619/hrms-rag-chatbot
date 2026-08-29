# 🏢 Enterprise AI HR Assistant (WhatsApp Hybrid RAG Chatbot)

An enterprise-grade, multi-turn AI HR Assistant deployed over WhatsApp with an integrated Flask Admin Portal.

---

## 📖 Project Overview

Managing employee queries regarding leave policies, payslip access, balance inquiries, and training materials often places a significant burden on HR departments. This project provides an automated, self-service HR Assistant accessible directly via WhatsApp and backed by a centralized web-based management portal.

* **What it does:** Automates HR policy query handling, leave balance tracking, multi-turn leave application workflows, password-protected payslip retrieval, and training video delivery.
* **Problems it solves:** Eliminates repetitive HR support tickets, prevents leave policy ambiguity, secures sensitive employee payroll documents, and provides high-availability assistance.
* **Target Users:** Enterprise employees (via WhatsApp) and HR Administrators/Managers (via the Web Admin Portal).

---

## ✨ Key Features

* **WhatsApp Conversational Interface:**
  * **Interactive Greeting & Action Menu:** Dynamic interactive list pickers and button menus for quick navigation.
  * **Multi-Turn Leave Application Workflow:** Slot-filling mechanism to capture start date, end date, and reason with explicit interactive confirmation buttons.
  * **Deterministic Leave Classification:** Rule-based detection for Casual, Sick, and Critical leave requests, backed by policy validation.
  * **Leave Balance & History Inquiries:** Real-time database queries to fetch remaining quotas and historical application statuses.
  * **Password-Protected Payslip Delivery:** Automatic PDF encryption with credentials (`<EMPLOYEE_ID>@<LAST_4_DIGITS_PHONE>`) delivered via AWS S3 presigned URLs or direct documents.
  * **Training Video Dispatch:** Category-based retrieval and delivery for Safety, Health Insurance, and Induction modules.

* **Hybrid RAG Policy Search:**
  * **Dense Retrieval:** Native PostgreSQL `pgvector` HNSW index with cosine distance (`sentence-transformers/all-MiniLM-L6-v2` via FastEmbed).
  * **Sparse Retrieval:** In-memory BM25Okapi keyword search over synchronized policy chunks.
  * **Multi-Query Expansion:** Generates alternative policy queries to boost semantic recall.
  * **Reciprocal Rank Fusion (Math RRF):** Combines and scores dense and sparse candidates.
  * **Semantic Caching:** Caches high-similarity queries ($\ge 0.92$) in Upstash Redis to minimize latency and token consumption.
  * **Cascading LLM Engine:** Primary generation via Google Gemini 2.5 Flash with automatic failover to Groq (`llama-3.3-70b-versatile`) on rate limits (`429`/`503`) and raw chunk fallback formatting.

* **Flask Admin Portal:**
  * **Dashboard Analytics:** Live headcount, pending leave counters, upload statistics, and audit activity logs.
  * **Employee Management:** Directory listing, live leave status, search filters, additions, updates, and removals.
  * **Leave Request Approval Flow:** Manager approval/rejection actions triggering direct WhatsApp status alerts to employees.
  * **Payroll Uploads & Bulk Processing:** Single payslip uploads and background ZIP processing with Redis Queue (RQ) and daemon thread fallbacks.
  * **Policy Vector Management:** Upload PDFs to S3 with automated asynchronous text extraction, chunking, pgvector embedding, and cache clearing.
  * **Training Video Management:** Video cataloging and metadata persistence linked to S3 objects.

* **Observability & Telemetry:**
  * **Langfuse Tracing:** Distributed OpenTelemetry-compliant spans capturing retrieval latency, token usage, cache hits, generation logs, and feedback scores.

---

## 🔄 How the Project Works

1. **Inbound Webhook:** A user sends a WhatsApp message or interacts with a menu/button, which hits the `/webhook` endpoint.
2. **Authentication & Rate Limiting:** The webhook verifies rate limits and looks up the employee's registered WhatsApp phone number in PostgreSQL.
3. **Intent Routing & Session Handling:**
   * If an active leave application session exists, the conversation controller continues slot filling.
   * If the intent is payroll, balance, or video-related, dedicated database/S3 services process the request.
   * If the user asks a policy question, the query enters the Hybrid RAG Pipeline.
4. **Hybrid RAG Execution:**
   * The semantic cache is queried in Upstash Redis.
   * On a cache miss, query expansion generates variations evaluated against `pgvector` and `BM25Okapi`.
   * Candidates are re-ranked using Math RRF.
   * The context is synthesized using Gemini 2.5 Flash (or Groq failover).
5. **Response Delivery:** The structured answer and source citations are formatted and dispatched back to the user via the WhatsApp Cloud API.
6. **Telemetry:** Execution metrics, latency, and token consumption are asynchronously logged to Langfuse.

---

## 🏗️ System Architecture

```mermaid
flowchart TD
    subgraph Client Layer
        WA[WhatsApp User]
        Admin[HR Admin Browser]
    end

    subgraph Flask Application Gateway
        WH["/webhook (WhatsApp Cloud API)"]
        Router[Intent & Session Router]
        AdminRoutes["/admin Routes (Flask Blueprint)"]
    end

    subgraph Core Services
        LeaveSvc[Leave Service]
        MediaSvc[Salary & Video Service]
        RAGSvc[Hybrid RAG Engine]
        SyncSvc[Policy Sync Service]
        Worker[Background Tasks / RQ Worker]
    end

    subgraph Data & Storage Layer
        PG[(PostgreSQL + pgvector)]
        Redis[(Upstash Redis / Cache & Memory)]
        S3[(AWS S3 Bucket)]
    end

    subgraph External AI & Telemetry
        Gemini[Google Gemini 2.5 Flash]
        Groq[Groq Secondary LLM]
        Langfuse[Langfuse Observability]
    end

    WA -->|Inbound Message| WH
    WH --> Router
    Router -->|Leave Action| LeaveSvc
    Router -->|Salary / Video| MediaSvc
    Router -->|Policy Query| RAGSvc

    Admin --> AdminRoutes
    AdminRoutes --> SyncSvc
    AdminRoutes --> Worker

    LeaveSvc <--> PG
    MediaSvc <--> PG
    MediaSvc <--> S3

    RAGSvc <--> Redis
    RAGSvc <--> PG
    RAGSvc -->|Prompt| Gemini
    Gemini -.->|Failover on 429/503| Groq
    RAGSvc -->|Log Spans| Langfuse

    SyncSvc --> S3
    SyncSvc --> PG
    SyncSvc --> Redis
    Worker --> S3
    Worker --> PG

💻 Tech StackCategoryTechnologyPurposeBackend FrameworkPython 3.11, FlaskApplication routing, webhook receiver, session management, and admin APIFrontend / TemplatesHTML5, Jinja2, CSS3, JavaScriptAdmin dashboard interface, forms, dynamic styling, and componentsDatabasePostgreSQL, pgvectorRelational tables and native vector storage with HNSW cosine indexingDatabase Driverpsycopg2-binaryLow-level connection handling and batch operations (execute_values)Cache & MemoryUpstash RedisFast semantic vector cache ($\ge 0.92$ similarity) and rolling chat historyObject StorageAWS S3 (boto3)Document, payslip, and training video storage with presigned URLsVector EmbeddingsFastEmbed (sentence-transformers/all-MiniLM-L6-v2)Document chunk and query embedding generation (384 dimensions)Sparse Retrievalrank-bm25 (BM25Okapi)In-memory lexical/keyword search across ingested policy documentsPrimary LLMGoogle Gemini 2.5 Flash (google-genai)High-speed response generation, query expansion, and classification fallbackSecondary LLMGroq API (groq)Dynamic high-throughput failover for primary rate limits or outagesPDF Processingpypdf, langchain-communityText extraction, recursive chunking, and AES-128 user password encryptionBackground QueueRedis Queue (rq), Python threadingAsynchronous bulk payslip encryption and PDF vector synchronizationObservabilityLangfuse SDK (v3/v4 OTel)Span tracing, generation tracking, latency monitoring, and token metricsMessaging APIMeta WhatsApp Cloud APIMulti-channel interactive messaging (lists, buttons, documents, video)📁 Project StructurePlaintext.
├── app/
│   ├── routes/
│   │   └── admin_routes.py           # Blueprint for dashboard, employee, leave, salary, and policy management
│   ├── services/
│   │   ├── auth_service.py           # Admin authentication logic
│   │   ├── intent_service.py         # Regex, keyword, and Gemini fallback intent classifier
│   │   ├── leave_service.py          # Multi-turn slot-filling leave application and balance handlers
│   │   ├── media_service.py          # Salary slips, training videos, and interactive greetings
│   │   ├── memory_service.py         # Upstash Redis conversation history management
│   │   ├── policy_sync_service.py    # S3 PDF download, chunking, FastEmbed vectorization, and cache reset
│   │   ├── rag_service.py            # Hybrid retrieval (pgvector + BM25), Math RRF, and cascading LLM logic
│   │   ├── s3_service.py             # AWS S3 storage client, presigned URLs, and file operations
│   │   ├── semantic_cache_service.py # Redis vector similarity caching for policy queries
│   │   ├── setup_pgvector.py         # Database vector migration and initial S3 indexing script
│   │   ├── telemetry_service.py      # Centralized Langfuse OpenTelemetry instrumentation
│   │   └── whatsapp_service.py       # WhatsApp Cloud API helper (text, buttons, lists, media)
│   ├── tasks/
│   │   ├── salary_tasks.py           # Bulk payslip ZIP unpack, encryption, S3 upload, and DB save
│   │   └── worker_tasks.py           # Background workers for heavy workloads
│   └── utils/
│       └── pdf_security.py           # Password generation and AES-128 PDF stream encryption
├── eval/
│   ├── benchmark_data.json           # 25-question ground-truth evaluation dataset
│   └── run_evaluation.py             # Benchmark evaluation suite (Hit Rate, MRR, Faithfulness, Relevancy)
├── scripts/                          # Utility and maintenance automation scripts
├── static/
│   ├── css/
│   │   └── style.css                 # Admin dashboard styling
│   └── js/                           # Admin frontend interactive scripts
├── templates/
│   ├── base.html                     # Base template wrapper (Navbar, Sidebar, Flash notifications)
│   ├── login.html                    # Admin login portal
│   ├── dashboard.html                # Main metrics overview dashboard
│   ├── employees.html                # Employee directory listing and search
│   ├── add_employee.html             # New employee registration form
│   ├── edit_employee.html            # Update existing employee details
│   ├── leave_requests.html           # Leave request approvals and history table
│   ├── upload_salary.html            # Individual and bulk payslip management view
│   ├── policy_management.html        # PDF policy upload, re-indexing, and deletion view
│   └── upload_video.html             # Training video upload and cataloging form
├── config.py                         # Application configuration settings
├── database.py                       # PostgreSQL initialization, queries, and schema helpers
├── leave_session.py                  # In-memory dictionary storing active multi-turn leave sessions
├── main.py                           # Application entry point and WhatsApp webhook server
├── rate_limiter.py                   # In-memory rate limiting for WhatsApp senders
├── validators.py                     # Data validation routines (phone numbers, date ranges, leave days)
├── clear_all_cache.py                # Redis cache invalidation utility
├── requirements.txt                  # Python dependencies
└── .env                              # Environment configuration file
📋 PrerequisitesBefore running the application locally, ensure you have:Python: Version 3.11 or higherPostgreSQL: Version 15+ with the pgvector extension installedRedis: A running local Redis instance (for RQ background workers) and an Upstash Redis REST instance (for semantic caching/memory)Cloud Accounts & API Keys:Meta for Developers Account (WhatsApp Cloud API credentials)Google AI Studio API Key (Gemini 2.5 Flash)Groq Cloud API KeyAWS Account (S3 bucket with read/write credentials)Langfuse Account (Public & Secret API Keys)⚙️ Installation & Setup1. Clone the RepositoryBashgit clone <repository-url>
cd <project-folder>
2. Create and Activate a Virtual EnvironmentBash# Using venv
python -m venv venv
source venv/bin/activate  # On Windows: .\venv\Scripts\activate

# Or using Conda
conda create -n hrms python=3.11 -y
conda activate hrms
3. Install DependenciesBashpip install -r requirements.txt
4. Configure Environment VariablesCreate a .env file in the project root:Bashcp .env.example .env
Populate the configuration values according to the Environment Variables section.5. Initialize the Database & Ingest PoliciesRun the vector initialization script to create required tables, configure the HNSW index, and ingest policy documents from AWS S3:Bashpython app/services/setup_pgvector.py
🔐 Environment VariablesEnsure all necessary variables are configured inside your .env file:VariableDescriptionExample / DefaultDATABASE_URLPostgreSQL connection string with pgvector supportpostgresql://user:password@localhost:5432/hrms_dbREDIS_URLRedis URL for local RQ background job queueredis://localhost:6379/0UPSTASH_REDIS_REST_URLUpstash Redis REST endpoint for cache and memoryhttps://your-instance.upstash.ioUPSTASH_REDIS_REST_TOKENUpstash Redis REST authentication tokenyour_upstash_tokenGEMINI_API_KEYGoogle Gemini API key for primary RAG generationAIzaSy...GROQ_API_KEYGroq Cloud API key for failover LLM executiongsk_...AWS_ACCESS_KEY_IDAWS IAM Access Key ID with S3 permissionsAKIA...AWS_SECRET_ACCESS_KEYAWS IAM Secret Access Keyyour_aws_secretAWS_REGIONAWS S3 regionap-southeast-2S3_BUCKET_NAMEAWS S3 bucket name for policies, payslips, and videosyour-hrms-bucketS3_POLICY_PREFIXS3 directory prefix where policy PDFs residepolicies/WHATSAPP_TOKENMeta WhatsApp Cloud API access tokenEAAG...PHONE_NUMBER_IDWhatsApp Business Phone Number ID109876543210987VERIFY_TOKENWebhook verification token configured in Meta Appyour_custom_verify_tokenLANGFUSE_PUBLIC_KEYLangfuse public project keypk-lf-...LANGFUSE_SECRET_KEYLangfuse secret project keysk-lf-...LANGFUSE_BASE_URLLangfuse host URLhttps://cloud.langfuse.comSECRET_KEYFlask secret key for admin session managementyour-secure-secret-keyPORTWeb server listening port5000🚀 Running the Application1. Start the Background Worker (Redis Queue)In a dedicated terminal, start the worker to handle asynchronous bulk payroll tasks:Bashrq worker hr_tasks --url redis://localhost:6379/0
2. Start the Flask ApplicationIn another terminal, start the primary webhook and admin server:Bashpython main.py
The application will be accessible at:Admin Portal: http://localhost:5000/adminWhatsApp Webhook: http://localhost:5000/webhookHealth Check: http://localhost:5000/health📡 API DocumentationWebhook & Public EndpointsMethodEndpointDescriptionAuth RequiredGET/webhookWhatsApp Cloud API webhook handshake verification (hub.challenge)Verification TokenPOST/webhookInbound WhatsApp message and interactive response receiverNone (Meta Webhook)GET/healthApplication and database connectivity health probeNoneGET/videos/<filename>Serves local video files (if configured)NoneAdmin Portal Endpoints (/admin)MethodEndpointDescriptionAuth RequiredGET/Root route redirecting to login or dashboardNoGET, POST/admin/loginAdmin authentication portalNoGET/admin/logoutTerminates admin sessionYes (Session)GET/adminMain operational analytics dashboardYes (Session)GET/employeesList and search registered employeesYes (Session)GET, POST/add-employeeForm to register a new employeeYes (Session)GET, POST/edit-employee/<id>Modify employee detailsYes (Session)GET/delete-employee/<id>Remove employee and cascade child recordsYes (Session)GET/leave-requestsView leave requests with status filtersYes (Session)GET/approve-leave/<id>Approve leave request and notify employee on WhatsAppYes (Session)GET/reject-leave/<id>Reject leave request and notify employee on WhatsAppYes (Session)GET, POST/upload-salaryUpload and encrypt an individual employee payslipYes (Session)POST/upload-bulk-salaryUpload ZIP archive for background bulk payslip encryptionYes (Session)GET/view-salary/<id>Redirects to an S3 presigned URL for the payslipYes (Session)GET/delete-salary/<id>Deletes payslip record and S3 objectYes (Session)GET, POST/policy-managementUpload policy PDF (dispatches background vector indexing)Yes (Session)GET, POST/delete-policy/<name>Deletes policy from DB, S3, vectors, and invalidates cacheYes (Session)GET/download-policy/<name>Downloads policy PDF via presigned URL attachmentYes (Session)GET/view-policy/<name>Opens policy PDF inline via presigned URLYes (Session)GET, POST/upload-videoUploads training video MP4 to S3 with categoryYes (Session)GET, POST/delete-video/<id>Removes video record and S3 objectYes (Session)🗃️ DatabaseThe application utilizes PostgreSQL with the pgvector extension for storing application metadata and high-dimensional document vectors.Code snippeterDiagram
    employees ||--o{ leave_requests : "submits"
    employees ||--o{ salary_slips : "owns"
    employees ||--o| leave_balance : "maintains"
    leave_types ||--o{ leave_requests : "categorizes"

    employees {
        varchar employee_id PK
        varchar name
        varchar whatsapp UK
        varchar manager
        varchar department
    }

    leave_types {
        serial id PK
        varchar leave_name UK
        integer yearly_limit
    }

    leave_balance {
        varchar employee_id PK, FK
        integer casual
        integer sick
        integer earned
    }

    leave_requests {
        serial id PK
        varchar employee_id FK
        varchar from_date
        varchar to_date
        integer leave_days
        varchar leave_type FK
        text reason
        varchar category
        varchar priority
        varchar status
        timestamp applied_at
    }

    salary_slips {
        serial id PK
        varchar employee_id FK
        integer month
        integer year
        text file_path
    }

    training_videos {
        serial id PK
        varchar title
        varchar category
        text description
        text s3_key
        timestamp uploaded_at
    }

    policy_files {
        serial id PK
        varchar file_name UK
        text s3_key
        varchar version
        varchar file_hash
        timestamp upload_time
        varchar status
    }

    policy_vectors {
        serial id PK
        text content
        jsonb metadata
        vector embedding
    }

    activity_logs {
        serial id PK
        text activity
        timestamp created_at
    }
🔒 Authentication & AuthorizationAdmin Portal Authentication: Protected via session-based authentication. Unauthenticated requests to protected /admin or management routes trigger redirects to /admin/login.WhatsApp User Authentication: Zero-friction phone identity validation against the registered employees database table. Unregistered phone numbers receive an access denial message.Document Security: Salary slip PDFs are encrypted using AES-128 user encryption before being stored in AWS S3 or dispatched. Passwords follow the format: <EMPLOYEE_ID>@<LAST_4_DIGITS_OF_PHONE>.🖼️ Screenshots / DemoAdmin Login(Placeholder: screenshots/login.png)Operational Dashboard(Placeholder: screenshots/dashboard.png)Employee Directory(Placeholder: screenshots/employees.png)Leave Approvals(Placeholder: screenshots/leave_requests.png)🧪 Testing & EvaluationThe repository includes a standalone automated evaluation suite configured to benchmark retrieval and generation performance using a 25-question ground-truth dataset.Run the evaluation suite:Bashpython eval/run_evaluation.py
Metrics Assessed:Hit Rate @ 5: Proportion of queries where the ground-truth policy chunk is retrieved in the top 5 candidates.Mean Reciprocal Rank (MRR): Measures the ranking position of the primary relevant document.Faithfulness Score (0.0 to 1.0): LLM Judge evaluation assessing whether the generated response is strictly grounded in the retrieved context.Answer Relevancy (0.0 to 1.0): Measures how directly and completely the response answers the employee's query.🛡️ Error Handling & SecurityCascading Failover: Automatic fallback from Gemini 2.5 Flash to Groq (llama-3.3-70b-versatile) when encountering 429 (Quota Exceeded) or 503 (Service Unavailable) status codes.Raw Chunk Fallback: Formats and returns bulleted policy excerpts if all LLM APIs experience an outage.Rate Limiting: Enforces in-memory request thresholds per sender number to prevent abuse.Duplicate Webhook Suppression: In-memory caching of processed WhatsApp message IDs within a 60-second sliding window to avoid processing retries twice.Input Sanitization: Parameterized SQL queries using psycopg2 across all database handlers to prevent SQL injection vulnerabilities.⚡ Performance & ScalabilitySemantic Caching: Vector similarity matching ($\ge 0.92$) via Upstash Redis reduces LLM generation costs and yields response times under 500ms for repeated or semantically equivalent questions.HNSW Indexing: Hierarchical Navigable Small World graphs enable rapid approximate nearest neighbor vector searches across policy embeddings.Asynchronous Offloading: CPU-intensive workloads (PDF chunking, vector embedding, bulk AES-128 encryption) are offloaded to Redis Queue (RQ) workers or daemon threads, ensuring instant HTTP 200 responses to webhooks and admin users.🚀 DeploymentThe project can be deployed to containerized and cloud platforms:Web Service (Render / Railway / AWS EC2): Run the Flask application via Gunicorn:Bashgunicorn -w 4 -b 0.0.0.0:5000 main:app
Background Worker Service: Deploy a separate worker container executing:Bashrq worker hr_tasks --url $REDIS_URL
Managed Databases: PostgreSQL with pgvector hosted on Neon, Supabase, or AWS RDS.File Storage: AWS S3 configured with private bucket policies and IAM credentials.🔮 Future Improvements[ ] Add multi-language translation support for WhatsApp responses.[ ] Implement OAuth2 / Google Workspace SSO for admin portal login.[ ] Integrate automated WhatsApp template broadcasts for company-wide policy updates.[ ] Support voice note queries via speech-to-text processing.⚠️ Known LimitationsActive leave sessions are stored in an in-memory dictionary (leave_session.py), which resets if the server restarts during a multi-turn conversation.Rate limiter counters are kept in memory and do not persist across multi-instance horizontal deployments unless shifted to Redis.🤝 ContributingFork the repository.Create a feature branch:Bashgit checkout -b feature/YourFeature
Commit your changes:Bashgit commit -m "Add your feature description"
Push to the branch:Bashgit push origin feature/YourFeature
Open a Pull Request.📄 LicenseLicense: Not specified👤 AuthorAuthor: [Your Name]🙏 AcknowledgementsMeta WhatsApp Cloud APIpgvectorFastEmbed by QdrantGoogle GenAI SDKGroq CloudLangfuseUpstash Redis🏷️ GitHub Repository Topicswhatsapp-chatbot, rag, pgvector, hybrid-search, bm25, gemini-api, groq, langfuse, flask, hrms, semantic-cache, fastembed, redis-queue, opentelemetry
