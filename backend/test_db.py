import psycopg2
from dotenv import load_dotenv
import os

# Load secrets from .env file
load_dotenv()

# Read the database address we saved earlier
db_url = os.getenv("DATABASE_URL")

# Try to connect
try:
    conn = psycopg2.connect(db_url)
    print("✅ Successfully connected to Postgres!")
    conn.close()
except Exception as e:
    print("❌ Connection failed:")
    print(e)