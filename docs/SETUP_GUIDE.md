SETUP AND DEPLOYMENT GUIDE
WhatsApp HR Assistant with RAG

===============================================================================
1. LOCAL DEVELOPMENT SETUP
===============================================================================

1.1 Prerequisites
- Python 3.11 or higher
- pip (Python package manager)
- Git
- Cloudflare account (free tier: cloudflared)
- Meta WhatsApp Business Account (for testing)

1.2 Installation Steps

Step 1: Clone and navigate to project
```bash
git clone <repository>
cd rag_demo
```

Step 2: Create virtual environment
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

Step 3: Install dependencies
```bash
pip install -r requirements.txt
```

Step 4: Setup environment variables
```bash
cp .env.example .env
# Edit .env with your credentials
nano .env
```

Step 5: (Optional) Create PDF from sample policy
```bash
# The app includes demo chunks, but for full experience:
# 1. Copy SAMPLE_HR_POLICY.txt content
# 2. Use an online converter (e.g., Zamzar) to convert to PDF
# 3. Save as hr_policy.pdf in project root
```

Step 6: Test with demo data
```bash
python app.py
# Should see: "[INFO] Index built: 10 demo chunks"
```

===============================================================================
2. ANTHROPIC API SETUP
===============================================================================

2.1 Get API Key

1. Visit https://console.anthropic.com
2. Sign up / Log in
3. Navigate to "API Keys"
4. Click "Create Key"
5. Copy the key

2.2 Add to .env
```bash
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxx
```

2.3 Test API Connection
```bash
python -c "
import anthropic
import os
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
response = client.messages.create(
    model='claude-opus-4-5',
    max_tokens=100,
    messages=[{'role': 'user', 'content': 'Say hello'}]
)
print('✓ API working:', response.content[0].text)
"
```

===============================================================================
3. WHATSAPP SETUP (Meta Developer Platform)
===============================================================================

3.1 Create Business Account

1. Go to https://business.facebook.com
2. Click "Create Account"
3. Fill in business details
4. Verify email and phone

3.2 Create an App

1. Go to https://developers.facebook.com/apps
2. Click "Create App"
3. Select "Business"
4. Choose "WhatsApp" product
5. Complete setup wizard

3.3 Get WhatsApp Credentials

After creating the app, you'll get:
- WABA_ID: WhatsApp Business Account ID
- PHONE_NUMBER_ID: Phone number used for sending messages
- WHATSAPP_TOKEN: API access token (24-hour temporary)

**IMPORTANT:** Generate a permanent token using System User:
1. Go to Meta Business Settings → Users → System Users
2. Click "Generate Token"
3. Select your WhatsApp app
4. Choose "Never" for expiration
5. Copy the token

3.4 Add to .env
```bash
WHATSAPP_TOKEN=<permanent_system_user_token>
PHONE_NUMBER_ID=<your_phone_number_id>
WABA_ID=<your_waba_id>
VERIFY_TOKEN=<any_random_string>  # e.g., "my_verify_token_12345"
```

3.5 Test Sending a Message
```bash
python -c "
import os
import requests
from dotenv import load_dotenv

load_dotenv()
to = os.getenv('PHONE_NUMBER_ID')  # Replace with test recipient number
requests.post(
    f'https://graph.facebook.com/v25.0/{os.getenv(\"PHONE_NUMBER_ID\")}/messages',
    headers={'Authorization': f'Bearer {os.getenv(\"WHATSAPP_TOKEN\")}'},
    json={
        'messaging_product': 'whatsapp',
        'to': '<recipient_number_with_country_code>',
        'type': 'text',
        'text': {'body': 'Test message'}
    }
)
print('✓ Message sent (check WhatsApp)')
"
```

===============================================================================
4. CLOUDFLARE TUNNEL SETUP
===============================================================================

4.1 Install Cloudflare CLI

MacOS:
```bash
brew install cloudflared
```

Linux:
```bash
curl -L --output cloudflared.tgz https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.tgz
tar -xzf cloudflared.tgz
sudo mv cloudflared /usr/local/bin
```

Windows:
- Download from: https://github.com/cloudflare/cloudflared/releases
- Add to PATH

4.2 Create Tunnel

```bash
# Authenticate with Cloudflare
cloudflared tunnel login

# List existing tunnels
cloudflared tunnel list

# Create named tunnel (recommended)
cloudflared tunnel create whatsapp-hr-assistant
```

4.3 Start Tunnel

```bash
# Terminal 1: Start Flask app
python app.py

# Terminal 2: Start tunnel (keep running)
cloudflared tunnel --url http://localhost:5000 run whatsapp-hr-assistant
```

Note: First run is temporary; for production, use named tunnel configuration.

4.4 Get Public URL

When tunnel starts, you'll see:
```
https://abc123.trycloudflare.com -> http://localhost:5000
```

Copy this URL for webhook configuration.

===============================================================================
5. WEBHOOK CONFIGURATION
===============================================================================

5.1 Configure in Meta Dashboard

1. Go to WhatsApp → Configuration
2. Webhook URL: Paste your Cloudflare URL
   Example: https://abc123.trycloudflare.com/webhook
3. Verify Token: Must match VERIFY_TOKEN in .env
4. Click "Verify and Save"

5.2 Subscribe Webhook (CRITICAL)

This step is often missed but is required for messages to arrive:

```bash
#!/bin/bash
# save as subscribe_webhook.sh

WABA_ID="your_waba_id"
WHATSAPP_TOKEN="your_whatsapp_token"

curl -X POST https://graph.facebook.com/v25.0/${WABA_ID}/subscribed_apps \
  -H "Authorization: Bearer ${WHATSAPP_TOKEN}"
```

Run it:
```bash
chmod +x subscribe_webhook.sh
./subscribe_webhook.sh
```

Expected response:
```json
{"success": true}
```

5.3 Verify Webhook Receives Messages

1. Send a message to your WhatsApp bot number
2. Check Flask console for: "[INFO] Received message from..."
3. You should receive a response in WhatsApp

If no messages arrive, check:
- Cloudflare tunnel is running
- VERIFY_TOKEN matches in Meta dashboard
- Webhook subscription was successful
- WhatsApp number is in test mode (if applicable)

===============================================================================
6. TESTING SCENARIOS
===============================================================================

6.1 Test Leave Query

**Send:** "How many casual leave days do I get?"
**Expected:** Returns leave entitlement from policy

**Send:** "What's the leave application process?"
**Expected:** RAG retrieves and answers with policy context

6.2 Test Leave Application

**Send:** "I want to apply for 2 days of leave from 2024-02-15"
**Expected:** Extracts dates and confirms application submitted

6.3 Test Salary Query

**Send:** "When is my salary processed?"
**Expected:** Returns salary processing date (25th of month)

6.4 Test Hallucination Prevention

**Send:** "What's the dress code policy?"
**Expected:** "I don't have information about dress code in the HR policy..."

6.5 Test Intent Classification

**Send:** "leave remaining" (intent: leave_balance)
**Send:** "apply leave" (intent: apply_leave)
**Send:** "salary slip" (intent: salary_slip)
**Send:** "Any text" (intent: rag)

===============================================================================
7. PRODUCTION DEPLOYMENT
===============================================================================

7.1 Server Requirements

- Linux VPS (Ubuntu 20.04+) or cloud VM (AWS, GCP, Azure)
- 1GB RAM (minimum)
- 10GB SSD (for logs and potential scaling)
- Python 3.11+
- Port 5000 accessible (or configure differently)

7.2 Deploy on AWS EC2

Step 1: Launch Ubuntu 20.04 instance
```bash
# SSH into instance
ssh -i key.pem ubuntu@instance-ip

# Update system
sudo apt update && sudo apt upgrade -y

# Install Python
sudo apt install python3.11 python3.11-venv python3-pip -y
```

Step 2: Clone and setup
```bash
git clone <repo>
cd rag_demo
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Step 3: Create .env with production credentials
```bash
sudo nano .env
# Add all credentials
```

Step 4: Setup systemd service
```bash
# Create service file
sudo nano /etc/systemd/system/whatsapp-hr.service

# Paste:
[Unit]
Description=WhatsApp HR Assistant
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/rag_demo
ExecStart=/home/ubuntu/rag_demo/venv/bin/python app.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target

# Enable and start
sudo systemctl enable whatsapp-hr
sudo systemctl start whatsapp-hr

# Check status
sudo systemctl status whatsapp-hr
```

7.3 Setup Cloudflare Tunnel for Production

```bash
# Create persistent tunnel configuration
cloudflared tunnel create whatsapp-hr-prod

# Create config file
mkdir -p ~/.cloudflare-warp
nano ~/.cloudflare-warp/config.yml

# Paste:
tunnel: whatsapp-hr-prod
credentials-file: /home/ubuntu/.cloudflare-warp/whatsapp-hr-prod.json
ingress:
  - hostname: whatsapp-hr.yourdomain.com
    service: http://localhost:5000
  - service: http_status:404

# Start tunnel as service
cloudflared service install
sudo systemctl start cloudflared
```

7.4 Update Webhook in Meta Dashboard

1. Update webhook URL to production: https://whatsapp-hr.yourdomain.com/webhook
2. Re-verify webhook
3. Test with WhatsApp

7.5 Monitoring and Logs

```bash
# View Flask logs
sudo journalctl -u whatsapp-hr -f

# View Cloudflare tunnel logs
sudo journalctl -u cloudflared -f

# Monitor system resources
htop
```

===============================================================================
8. TROUBLESHOOTING
===============================================================================

8.1 "No messages received"

Checklist:
- [ ] Flask app is running (check terminal)
- [ ] Cloudflare tunnel is running and shows your URL
- [ ] Webhook URL is correct in Meta dashboard
- [ ] VERIFY_TOKEN matches in .env and Meta dashboard
- [ ] Webhook subscription was successful (run subscribe_webhook.sh)

Debug:
```bash
# Check if webhook is being called
tail -f app.log | grep "Received message"

# Verify Cloudflare tunnel is connected
cloudflared tunnel info whatsapp-hr-prod
```

8.2 "Token expired"

Error message: "Permanent access token not available"

Solution:
- You're using a 24-hour temporary token
- Go to Meta Business Settings → Users → System Users
- Generate a permanent token
- Update WHATSAPP_TOKEN in .env

8.3 "PDF not found"

Solution:
- App falls back to demo chunks automatically
- To use custom PDF:
  1. Convert SAMPLE_HR_POLICY.txt to PDF
  2. Save as hr_policy.pdf in project root
  3. Restart Flask app

8.4 "API rate limit exceeded"

Solution:
- Anthropic: Default limit is ~50 requests/minute
- Implement request queuing or caching for high traffic
- Contact Anthropic support for higher limits

8.5 "ChromaDB collection not found"

Error on startup: "collection 'hr_policies' does not exist"

Solution:
- Ensure PDF extraction is completing
- Check that chunks are being created
- Verify all dependencies installed: pip install -r requirements.txt
- Clear cache: rm -rf .chroma_db (if using persistent storage)

===============================================================================
9. TESTING CHECKLIST
===============================================================================

Before going to production, verify:

Development:
- [ ] API key works (test_api_connection.py)
- [ ] PDF extraction works (step1_extract.py)
- [ ] Chunking works with overlap (step2_chunk.py)
- [ ] Embeddings are generated (step3_embed.py)
- [ ] Index is built (step4_store.py)
- [ ] Full RAG pipeline works (step5_query.py)
- [ ] Hallucination prevention works (step6_wrong_question.py)

WhatsApp Integration:
- [ ] Flask webhook receives messages (/webhook POST)
- [ ] Messages are marked as read
- [ ] Text responses are sent back
- [ ] Webhook signature is valid (if Meta requires verification)

Intent Classification:
- [ ] RAG queries work ("How many leave days?")
- [ ] Leave balance queries work ("leaves left")
- [ ] Leave application works ("apply leave from...")
- [ ] Salary queries work ("salary slip")
- [ ] Unknown queries fallback to RAG

Performance:
- [ ] Query response time < 2 seconds
- [ ] Embedding similarity scores are reasonable (> 0.5)
- [ ] No memory leaks (monitor RAM usage over 1 hour)
- [ ] Error handling for missing documents

===============================================================================
10. SUPPORT AND RESOURCES
===============================================================================

Official Documentation:
- Anthropic API: https://docs.anthropic.com
- Meta WhatsApp API: https://developers.facebook.com/docs/whatsapp
- ChromaDB: https://docs.trychroma.com
- Cloudflare Tunnels: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/

Community:
- Anthropic Discord: https://discord.gg/anthropic
- LangChain GitHub: https://github.com/langchain-ai/langchain
- Stack Overflow: Tag [whatsapp-api], [rag], [chromadb]

Troubleshooting:
- Check logs: app.log, systemd journal
- Enable debug mode in Flask: app.run(debug=True)
- Test API connections independently
- Verify environment variables: env | grep -i whatsapp

===============================================================================

Need help? Contact support@nextai.tech or open an issue on GitHub.
Happy building! 🚀
