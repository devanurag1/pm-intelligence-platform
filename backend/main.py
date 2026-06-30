import json
import os

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI
from google import genai
import psycopg2
from pydantic import BaseModel
import requests

# Load environment variables & initialize clients
load_dotenv()
db_url = os.getenv("DATABASE_URL")
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

app = FastAPI()

# --- Pydantic Models ---

class CompanyCreate(BaseModel):
    name: str
    url: str

class ScrapeRequest(BaseModel):
    run_id: int
    url: str

class RunCreate(BaseModel):
    company_id: int

class ExtractRequest(BaseModel):
    run_id: int

# --- Helper Functions ---

def fetch_clean_text(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0"}  # pretends to be a real browser
    response = requests.get(url, headers=headers, timeout=10)
    soup = BeautifulSoup(response.text, "html.parser")

    # Remove tags that aren't useful text (scripts, styles, nav, footer)
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    text = soup.get_text(separator=" ", strip=True)
    return text

# --- API Routes ---

@app.get("/")
def root():
    return {"message": "PM Intelligence Platform is running"}

@app.post("/companies")
def create_company(company: CompanyCreate):
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO companies (name, url) VALUES (%s, %s) RETURNING id",
        (company.name, company.url)
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return {"id": new_id, "name": company.name, "url": company.url}

@app.get("/companies/{company_id}")
def get_company(company_id: int):
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("SELECT id, name, url, created_at FROM companies WHERE id = %s", (company_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        return {"error": "Company not found"}
    return {"id": row[0], "name": row[1], "url": row[2], "created_at": str(row[3])}

@app.post("/runs")
def create_run(run: RunCreate):
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO research_runs (company_id) VALUES (%s) RETURNING id",
        (run.company_id,)
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return {"id": new_id, "company_id": run.company_id}

@app.post("/scrape")
def scrape_website(req: ScrapeRequest):
    try:
        clean_text = fetch_clean_text(req.url)
    except Exception as e:
        return {"error": str(e)}

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO raw_sources (run_id, source_type, source_url, raw_text) VALUES (%s, %s, %s, %s) RETURNING id",
        (req.run_id, "website", req.url, clean_text)
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    return {"id": new_id, "characters_scraped": len(clean_text), "preview": clean_text[:300]}

@app.post("/analyze/extract")
def extract_company_info(req: ExtractRequest):
    # Step A: Pull all raw scraped text for this run
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("SELECT raw_text FROM raw_sources WHERE run_id = %s", (req.run_id,))
    rows = cur.fetchall()

    if not rows:
        cur.close()
        conn.close()
        return {"error": "No raw_sources found for this run_id. Scrape something first."}

    combined_text = "\n\n".join(row[0] for row in rows if row[0])
    combined_text = combined_text[:8000]  # keep it within a safe size

    # Step B: Build the prompt
    prompt = f"""You are a product analyst. Based on the following website content,
extract structured information about this company.

Return ONLY valid JSON, no markdown formatting, no explanation text.
Use exactly this structure:

{{
  "company_description": "...",
  "business_model": "...",
  "target_users": "...",
  "pricing_info": "...",
  "key_features": ["...", "..."],
  "stated_mission": "..."
}}

If any field is unclear from the content, write "unclear" as the value.

WEBSITE CONTENT:
{combined_text}
"""

    # Step C: Call Gemini
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )

    raw_output = response.text.strip()

    # Gemini sometimes wraps JSON in ```json fences, strip those if present
    if raw_output.startswith("```"):
        raw_output = raw_output.strip("`")
        raw_output = raw_output.replace("json", "", 1).strip()

    # Step D: Try to parse it as JSON
    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError:
        cur.close()
        conn.close()
        return {"error": "Gemini did not return valid JSON", "raw_output": raw_output}

    # Step E: Save it to framework_outputs
    cur.execute(
        "INSERT INTO framework_outputs (run_id, framework_name, output_json) VALUES (%s, %s, %s) RETURNING id",
        (req.run_id, "extraction", json.dumps(parsed))
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    return {"id": new_id, "extracted_data": parsed}