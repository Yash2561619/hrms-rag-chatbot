# WhatsApp HR Assistant with RAG

A production-ready HR assistant powered by Retrieval Augmented Generation (RAG). This system ingests HR policy PDFs, constructs semantic vector indices, and handles WhatsApp queries with intelligent routing.

## Architecture Overview

```
WhatsApp Message
        ↓
    Flask Webhook
        ↓
Intent Classification (Keyword Matching)
        ↓
    ┌───────────────────────────────────────────┐
    │  RAG Query  │  Leave Balance  │  Apply    │
    │   Handler   │    Handler      │ Leave     │
    │             │                 │ Handler   │
    └───────────────────────────────────────────┘
        ↓
  ChromaDB Vector Search (3 most relevant chunks)
        ↓
  Claude LLM with Grounded Context
        ↓
  WhatsApp Response
```

## Tech Stack

- **Python 3.11+** – Runtime
- **pdfplumber** – PDF text extraction with spatial awareness
- **LangChain** – RecursiveCharacterTextSplitter for semantic chunking
- **ChromaDB** – Vector database with local embeddings
- **sentence-transformers** – all-MiniLM-L6-v2 (384-dim embeddings)
- **Anthropic Claude** – claude-opus-4-5 for reasoning
- **Flask** – Webhook server
- **WhatsApp Cloud API v25.0** – Message transport
- **Cloudflare Tunnel** – Secure public URL without port forwarding

## Quick Start

### 1. Setup

```bash
# Clone/navigate to project
cd rag_demo

# Install dependencies
pip install -r requirements.txt

# Setup environment
cp .env.example .env
# Edit .env with your credentials
```

### 2. Environment Variables

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-xxxxx
WHATSAPP_TOKEN=your_permanent_system_user_token
PHONE_NUMBER_ID=your_phone_number_id
WABA_ID=your_waba_id
VERIFY_TOKEN=your_custom_verify_token
HR_POLICY_PATH=hr_policy.pdf
```

**Important:** Use a **permanent System User token** from Meta Business Settings, not the 24-hour temporary token.

### 3. Run Locally

```bash
# Terminal 1: Start Flask server
python app.py

# Terminal 2: Expose with Cloudflare Tunnel
cloudflared tunnel --url http://localhost:5000
# Copy the URL generated
```

### 4. Configure WhatsApp Webhook

In Meta App Dashboard:

1. Go to WhatsApp → Configuration
2. Set Webhook URL to: `https://your-tunnel-url.trycloudflare.com/webhook`
3. Set Verify Token to match `VERIFY_TOKEN` in .env
4. Subscribe to `messages` webhook field

5. Subscribe webhook (replace WABA_ID):
```bash
curl -X POST https://graph.facebook.com/v25.0/{WABA_ID}/subscribed_apps \
  -H "Authorization: Bearer YOUR_WHATSAPP_TOKEN"
```

## Learning Path: Step-by-Step Demos

Run these in order to understand each RAG component:

### Step 1: PDF Extraction
```bash
python step1_extract.py
# Output: Raw text extracted from hr_policy.pdf
```

### Step 2: Chunking with Overlap
```bash
python step2_chunk.py
# Output: Semantic chunks preserving paragraph boundaries
# Shows why 500-char chunks + 50-char overlap matters
```

### Step 3: Embedding and Similarity
```bash
python step3_embed.py
# Output: 384-dim vectors with cosine similarity scores
# Shows how semantic search works
```

### Step 4: Vector Index Construction
```bash
python step4_store.py
# Output: ChromaDB collection with sample retrieval
# Demonstrates fast similarity search
```

### Step 5: Full Query Pipeline
```bash
python step5_query.py
# Output: End-to-end RAG with Claude
# First time using Anthropic API
```

### Step 6: Hallucination Prevention
```bash
python step6_wrong_question.py
# Output: Compare answers WITH context (RAG) vs WITHOUT (Hallucination)
# Shows why grounding context is critical
```

### Bonus: Knowledge Base Visualization
```bash
python show_knowledge_base.py
# Output: Terminal display of all indexed chunks
# Useful for debugging what the assistant "knows"
```

### Minimal RAG (30 lines)
```bash
# See rag_complete.py for a bare-bones RAG pipeline
# Useful for understanding the core algorithm
```

## Usage Examples

### In WhatsApp

**User:** "How many casual leave days do I get?"
```
Assistant: "Casual Leave: Employees are entitled to 12 days of casual leave 
per year. Casual leave must be applied for at least 2 days in advance."
```

**User:** "I want leave from 2024-02-15 to 2024-02-16"
```
Assistant: "✓ Leave Application Submitted:
• Type: Casual Leave
• From: 2024-02-15
• To: 2024-02-16
• Your manager will review shortly."
```

**User:** "What's the dress code?"
```
Assistant: "I don't have information about dress code in the HR policy. 
Please contact HR for this query."
```

## Key Design Decisions

### Why 500-Character Chunks?

The embedding model has a 256-token limit (~1,000 characters). Chunks of 500 characters:
- Stay well within token limits
- Maintain semantic density
- Reduce noise in retrieval

### Why 50-Character Overlap?

Multi-sentence policy rules often span paragraph boundaries. Overlap ensures:
- Complete rules captured in at least one chunk
- Conditions and subjects stay together
- No information loss at boundaries

### Why ChromaDB?

- Embeddings run entirely locally (no API calls)
- Fast similarity search on small-to-medium corpora (35-40 chunks)
- No database infrastructure needed
- Simple to reason about cosine distance

### Why Claude?

- Strong reasoning over policy text
- Better at grounded generation (doesn't hallucinate beyond context)
- Handles edge cases and ambiguous queries
- High-quality summaries and formatting

## Token Economics

| Approach | Tokens/Query | Tokens/Year (10k queries) |
|----------|------------|--------------------------|
| Full Document | 8,000 | 80,000,000 |
| RAG (3 chunks) | 500-800 | 5,000,000-8,000,000 |
| **Savings** | **90-94%** | **90-94%** |

## Architecture Components

### 1. Document Ingestion

```python
import pdfplumber

text = ""
with pdfplumber.open("hr_policy.pdf") as pdf:
    for page in pdf.pages:
        text += page.extract_text() + "\n"
```

**Why pdfplumber?** Preserves layout using spatial character model. More reliable than PyPDF2 for tables and columns.

### 2. Semantic Chunking

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter

splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=50,
    separators=["\n\n", "\n", ". ", " ", ""]  # Hierarchy
)
chunks = splitter.split_text(text)
```

**Why recursive?** Attempts paragraph split first, then sentences, then words. Preserves coherence better than fixed-length splitting.

### 3. Vector Embeddings

```python
from chromadb.utils import embedding_functions

ef = embedding_functions.DefaultEmbeddingFunction()
# Uses all-MiniLM-L6-v2: 384-dim, local, no API calls
vectors = ef(chunks)
```

**Model Properties:**
- 384-dimensional dense vectors
- Token limit: 256 tokens
- Cosine similarity for retrieval
- CPU-only, no GPU required

### 4. RAG Query Pipeline

```python
# Step 1: Retrieve
results = collection.query(query_texts=[query], n_results=3)
context = "\n\n".join(results["documents"][0])

# Step 2: Ground
system = "Answer ONLY using provided context. Don't infer."
user = f"Context:\n{context}\n\nQuestion: {query}"

# Step 3: Generate
response = claude.messages.create(
    model="claude-opus-4-5",
    max_tokens=1024,
    system=system,
    messages=[{"role": "user", "content": user}]
)
```

**Key principle:** The system prompt suppresses the LLM's parametric knowledge. Only tokens provided at inference time are used.

### 5. Message Routing

```python
def classify_intent(message: str) -> str:
    msg = message.lower()
    
    if any(k in msg for k in ["leaves left", "leave balance"]):
        return "leave_balance"
    if any(k in msg for k in ["apply for leave"]):
        return "apply_leave"
    # ... more patterns
    return "rag"  # Default to RAG
```

**No LLM call for routing.** Keyword matching is fast and deterministic.

### 6. WhatsApp Integration

```python
def send_text(to: str, body: str):
    requests.post(
        f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages",
        headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body}
        }
    )
```

**Webhook subscription is separate:** URL verification alone is insufficient. Must POST to `/subscribed_apps` endpoint.

## Troubleshooting

### Issue: "No messages received"

**Solution:** Ensure webhook is subscribed:
```bash
curl -X POST https://graph.facebook.com/v25.0/{WABA_ID}/subscribed_apps \
  -H "Authorization: Bearer YOUR_WHATSAPP_TOKEN"
```

### Issue: "Token expired"

**Solution:** Use permanent System User token, not 24-hour dashboard token.

### Issue: "PDF not found"

**Solution:** Falls back to demo chunks. Place `hr_policy.pdf` in project root or set `HR_POLICY_PATH` in .env.

### Issue: "No valid API key provided"

**Solution:** Ensure `ANTHROPIC_API_KEY` is set and valid in .env.

## Production Considerations

### Scaling

- **Small corpus (< 100 pages):** ChromaDB in-memory, suitable
- **Large corpus (> 100 pages):** Migrate to Pinecone, Weaviate, or Milvus
- **High traffic (> 100 queries/min):** Add Redis caching for repeated queries

### Security

- Use environment variables for all credentials
- Implement rate limiting on webhook
- Validate webhook signature from Meta
- Sanitize user input before RAG query
- Log all queries for audit trail

### Monitoring

- Track query latency (target: < 2 seconds)
- Monitor embedding quality (avg similarity score)
- Alert on repeated "I don't have information" responses
- Track intent classification accuracy

### Updates

- Rebuild index when HR policy changes
- Version control PDFs for policy change tracking
- A/B test chunk size and overlap for your domain

## Project Structure

```
rag_demo/
├── app.py                   # Main Flask webhook server
├── hr_policy.pdf            # Input HR policy document
├── step1_extract.py         # PDF extraction demo
├── step2_chunk.py           # Chunking demo
├── step3_embed.py           # Embedding demo
├── step4_store.py           # ChromaDB storage demo
├── step5_query.py           # Full query pipeline
├── step6_wrong_question.py  # Hallucination prevention
├── rag_complete.py          # 30-line minimal RAG
├── show_knowledge_base.py   # Knowledge base visualization
├── requirements.txt         # Python dependencies
├── .env.example            # Environment template
└── README.md               # This file
```

## Learning Resources

- [ChromaDB Docs](https://docs.trychroma.com)
- [Sentence Transformers](https://www.sbert.net)
- [LangChain Text Splitters](https://python.langchain.com/docs/modules/data_connection/document_transformers/)
- [Anthropic Claude API](https://docs.anthropic.com)
- [WhatsApp Cloud API](https://developers.facebook.com/docs/whatsapp/cloud-api)

## FAQ

**Q: Can I use a different LLM?**
A: Yes, replace `claude.messages.create()` with any OpenAI, Cohere, or Hugging Face API. Adjust token limits and system prompts accordingly.

**Q: How do I add more documents?**
A: Add PDFs to the ingestion pipeline and rebuild the index. For real-time updates, use a file watcher to trigger incremental indexing.

**Q: What if the policy changes?**
A: Rebuild the index with the updated PDF. The entire process takes < 1 second on CPU.

**Q: Can I use this for other domains?**
A: Yes! Replace the HR policy with any document corpus (customer support, knowledge base, legal docs). The pipeline is domain-agnostic.

## License

MIT

## Contact

For questions or issues, reach out to the development team.
