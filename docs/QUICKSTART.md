QUICK START GUIDE
WhatsApp HR Assistant - 5 Minute Setup

===============================================================================
OPTION 1: LOCAL TESTING (No WhatsApp)
===============================================================================

Goal: Test RAG pipeline with demo data locally

Step 1: Install dependencies
```bash
cd rag_demo
pip install -r requirements.txt
```

Step 2: Set API key
```bash
export ANTHROPIC_API_KEY="sk-ant-xxxxx"
```

Step 3: Run step-by-step demos
```bash
python step1_extract.py      # PDF extraction (uses demo)
python step2_chunk.py        # Semantic chunking
python step3_embed.py        # Embeddings & similarity
python step4_store.py        # Vector database
python step5_query.py        # Full RAG pipeline
python step6_wrong_question.py  # Hallucination prevention
```

Expected: Each step shows ✓ success message

This takes ~2 minutes and doesn't require WhatsApp credentials!

===============================================================================
OPTION 2: FULL SETUP (With WhatsApp)
===============================================================================

Prerequisites: Meta WhatsApp Business Account, Cloudflare account

Step 1: Clone and install (1 min)
```bash
cd rag_demo
pip install -r requirements.txt
cp .env.example .env
```

Step 2: Add credentials to .env (2 min)
```bash
# Get from Anthropic Console
ANTHROPIC_API_KEY=sk-ant-xxxxx

# Get from Meta WhatsApp Dashboard → System Users
WHATSAPP_TOKEN=your_permanent_token
PHONE_NUMBER_ID=your_phone_id
WABA_ID=your_waba_id
VERIFY_TOKEN=any_random_string
```

Step 3: Start Flask app (Terminal 1)
```bash
python app.py
# Expected: "[INFO] Index built: 10 demo chunks"
#           "Flask server starting on http://localhost:5000"
```

Step 4: Start Cloudflare tunnel (Terminal 2)
```bash
brew install cloudflared  # If not installed
cloudflared tunnel --url http://localhost:5000
# Expected: Shows URL like https://abc123.trycloudflare.com
```

Step 5: Configure webhook (1 min)
1. Copy Cloudflare URL from Terminal 2
2. Go to WhatsApp → Configuration in Meta Dashboard
3. Paste URL: https://abc123.trycloudflare.com/webhook
4. Verify Token: Must match VERIFY_TOKEN in .env
5. Click "Verify and Save"

Step 6: Subscribe webhook
```bash
curl -X POST https://graph.facebook.com/v25.0/{WABA_ID}/subscribed_apps \
  -H "Authorization: Bearer {WHATSAPP_TOKEN}"
# Expected: {"success": true}
```

Step 7: Test
Send message to your WhatsApp bot: "How many casual leave days?"
Expected: Bot responds with leave policy

Total time: ~5 minutes!

===============================================================================
COMMON ISSUES & FIXES
===============================================================================

Issue: "ModuleNotFoundError: No module named 'pdfplumber'"
Fix: pip install -r requirements.txt

Issue: "ANTHROPIC_API_KEY not set"
Fix: Ensure ANTHROPIC_API_KEY is in .env and do: source .env (or set manually)

Issue: "No messages received in WhatsApp"
Fix: Check 3 things:
  1. Flask is running: Look for "[INFO] Received message" in Terminal 1
  2. Cloudflare tunnel is running: Terminal 2 should show your URL
  3. Subscribe webhook was successful: Run the curl command above

Issue: "Token expired"
Fix: You're using a 24-hour temp token from Meta Dashboard.
     Generate a permanent System User token instead.

Issue: "ChromaDB error"
Fix: The app falls back to demo data automatically.
     To use custom PDF: Save as hr_policy.pdf in project root

===============================================================================
UNDERSTANDING THE ARCHITECTURE
===============================================================================

What happens when you send a message:

1. WhatsApp Message
   "How many leave days?"
   ↓
2. Flask Webhook Receives It
   (/webhook route in app.py)
   ↓
3. Intent Classification
   Keywords checked: Does it mention "leave", "apply", "salary"?
   Result: "rag" (general query)
   ↓
4. RAG Retrieval
   Query embedded with all-MiniLM-L6-v2
   ChromaDB finds 3 most similar chunks
   Example: "Casual leave: 12 days per year..."
   ↓
5. LLM Generation
   Context: Retrieved chunks
   System prompt: "Answer ONLY using context"
   Claude generates response
   ↓
6. Response Sent to WhatsApp
   Bot: "Casual Leave: Employees are entitled to 12 days..."

Total time: ~1-2 seconds

Key principle: Never invent information. Always ground in policy text.

===============================================================================
CUSTOMIZATION
===============================================================================

Change the HR Policy
```bash
1. Replace SAMPLE_HR_POLICY.txt
2. Convert to PDF: hr_policy.pdf
3. Restart Flask: python app.py
4. Index rebuilds automatically
```

Change the LLM
```python
# In app.py, replace:
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# With any other API (example: OpenAI)
from openai import OpenAI
openai = OpenAI(api_key=OPENAI_API_KEY)
```

Add More Intent Handlers
```python
# In classify_intent():
if any(k in msg for k in ["my benefits"]):
    return "benefits"

# Add handler:
def handle_benefits(sender: str, message: str):
    send_text(sender, "Your benefits package...")

# Add to router():
elif intent == "benefits":
    handle_benefits(sender, message)
```

Change Embedding Model
```python
# In app.py (advanced):
# Replace all-MiniLM-L6-v2 with:
# ef = embedding_functions.SentenceTransformerEmbeddingFunction(
#     model_name="all-mpnet-base-v2"  # Larger, more accurate
# )
```

===============================================================================
NEXT STEPS
===============================================================================

After quick start works:

1. Read README.md for architecture deep-dive
2. Read SETUP_GUIDE.md for production deployment
3. Customize the policy PDF for your organization
4. Add more intent handlers (benefits, policies, etc.)
5. Test error handling: Send queries outside the policy
6. Setup production server (AWS, GCP, Azure)
7. Monitor logs and latency
8. Setup backup and disaster recovery

===============================================================================
USEFUL COMMANDS
===============================================================================

# Check if Flask is running
ps aux | grep python | grep app.py

# Check Cloudflare tunnel status
cloudflared tunnel list

# View Flask logs
tail -f app.log

# Test API locally (no WhatsApp)
python step5_query.py

# Clear all indexes and rebuild
rm -rf .chroma_db
python app.py

# Generate requirements.txt (if you add packages)
pip freeze > requirements.txt

# Deactivate virtual environment
deactivate

===============================================================================

Questions? Check README.md or SETUP_GUIDE.md

Good luck! 🚀
