import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Flask
    SECRET_KEY = os.getenv(
        'FLASK_SECRET_KEY',
        'change-this-in-production'
    )

    # WhatsApp
    WHATSAPP_TOKEN = os.getenv('WHATSAPP_TOKEN')
    PHONE_NUMBER_ID = os.getenv('PHONE_NUMBER_ID')
    WABA_ID = os.getenv('WABA_ID')
    VERIFY_TOKEN = os.getenv('VERIFY_TOKEN')

    # Gemini
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

    # App
    BASE_URL = os.getenv(
        'BASE_URL',
        'http://localhost:5000'
    )

    # Upload folders
    POLICY_FOLDER = 'uploads/policies'
    SALARY_FOLDER = 'uploads/salary_slips'
    VIDEO_FOLDER = 'uploads/videos'

    # Database
    DB_PATH = os.path.join('data', 'employee.db')