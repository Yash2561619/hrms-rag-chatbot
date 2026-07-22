from main import app, init_chroma, init_gemini
from database import initialize_database
from scripts.update_db import build_index

# Initialize everything
initialize_database()
init_gemini()
build_index()
init_chroma()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)