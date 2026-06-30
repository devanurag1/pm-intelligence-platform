from fpdf import FPDF
import json
import os
from datetime import datetime

from bs4 import BeautifulSoup
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
from fastapi import FastAPI
from google import genai
from jinja2 import Template
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

class SwotRequest(BaseModel):
    run_id: int

class SynthesizeRequest(BaseModel):
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

@app.post("/analyze/swot")
def analyze_swot(req: SwotRequest):
    # Step A: Get the extraction data for this run (not raw text)
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute(
        "SELECT output_json FROM framework_outputs WHERE run_id = %s AND framework_name = %s",
        (req.run_id, "extraction")
    )
    row = cur.fetchone()

    if not row:
        cur.close()
        conn.close()
        return {"error": "No extraction found for this run_id. Run /analyze/extract first."}

    extraction_data = row[0]  # this is already a Python dict, psycopg2 converts JSONB automatically

    # Step B: Build the SWOT prompt
    prompt = f"""You are a senior product manager performing a SWOT analysis.
Based on the structured company data below, identify Strengths, Weaknesses,
Opportunities, and Threats.

- Strengths and Weaknesses are INTERNAL to the company (their product, team, positioning)
- Opportunities and Threats are EXTERNAL (market trends, competitors, risks)

Return ONLY valid JSON, no markdown formatting, no explanation text.
Use exactly this structure:

{{
  "strengths": ["...", "..."],
  "weaknesses": ["...", "..."],
  "opportunities": ["...", "..."],
  "threats": ["...", "..."]
}}

Give 3-5 items per category. Be specific, not generic.

COMPANY DATA:
{json.dumps(extraction_data)}
"""

    # Step C: Call Gemini
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )

    raw_output = response.text.strip()

    # Step D: Strip markdown fences if present (same Gemini quirk as before)
    if raw_output.startswith("```"):
        raw_output = raw_output.strip("`")
        raw_output = raw_output.replace("json", "", 1).strip()

    # Step E: Parse the JSON
    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError:
        cur.close()
        conn.close()
        return {"error": "Gemini did not return valid JSON", "raw_output": raw_output}

    # Step F: Save to framework_outputs
    cur.execute(
        "INSERT INTO framework_outputs (run_id, framework_name, output_json) VALUES (%s, %s, %s) RETURNING id",
        (req.run_id, "swot", json.dumps(parsed))
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    return {"id": new_id, "swot": parsed}

@app.post("/analyze/synthesize")
def synthesize(req: SynthesizeRequest):
    # Step A: Pull ALL framework outputs for this run
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute(
        "SELECT framework_name, output_json FROM framework_outputs WHERE run_id = %s",
        (req.run_id,)
    )
    rows = cur.fetchall()

    if not rows:
        cur.close()
        conn.close()
        return {"error": "No framework outputs found. Run extraction and swot first."}

    # Build a dict like {"extraction": {...}, "swot": {...}}
    all_data = {name: data for name, data in rows}

    # Step B: Build the synthesis prompt
    prompt = f"""You are a senior product manager preparing a complete strategic
analysis of this company, based on the research and frameworks below.

Generate the following, grounded specifically in the data provided (not generic advice):

1. feature_ideas: 4-5 new feature ideas, each with a one-sentence reasoning tied to a specific weakness or opportunity identified
2. metrics_to_track: 4-5 product metrics this company should track, with reasoning
3. experiments: 3-4 experiments (A/B tests or pilots) they could run, with hypothesis for each
4. roadmap: a simple 3-phase roadmap (Now / Next / Later) with 2-3 items per phase
5. interview_questions: 6-8 PM interview questions a candidate should practice if interviewing
   at this company, based on its actual strategic challenges (not generic PM questions)

Return ONLY valid JSON, no markdown formatting, no explanation text.
Use exactly this structure:

{{
  "feature_ideas": [{{"idea": "...", "reasoning": "..."}}],
  "metrics_to_track": [{{"metric": "...", "reasoning": "..."}}],
  "experiments": [{{"experiment": "...", "hypothesis": "..."}}],
  "roadmap": {{"now": ["..."], "next": ["..."], "later": ["..."]}},
  "interview_questions": ["...", "..."]
}}

RESEARCH DATA:
{json.dumps(all_data)}
"""

    # Step C: Call Gemini
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )

    raw_output = response.text.strip()

    if raw_output.startswith("```"):
        raw_output = raw_output.strip("`")
        raw_output = raw_output.replace("json", "", 1).strip()

    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError:
        cur.close()
        conn.close()
        return {"error": "Gemini did not return valid JSON", "raw_output": raw_output}

    cur.execute(
        "INSERT INTO framework_outputs (run_id, framework_name, output_json) VALUES (%s, %s, %s) RETURNING id",
        (req.run_id, "synthesis", json.dumps(parsed))
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    return {"id": new_id, "synthesis": parsed}

@app.get("/reports/{run_id}/markdown")
def generate_report(run_id: int):
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    # Get company info via the run
    cur.execute("""
        SELECT c.name, c.url FROM companies c
        JOIN research_runs r ON r.company_id = c.id
        WHERE r.id = %s
    """, (run_id,))
    company_row = cur.fetchone()
    if not company_row:
        cur.close()
        conn.close()
        return {"error": "Run not found"}

    company_name, company_url = company_row

    # Get all framework outputs for this run
    cur.execute(
        "SELECT framework_name, output_json FROM framework_outputs WHERE run_id = %s",
        (run_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    data = {name: output for name, output in rows}

    if "extraction" not in data or "swot" not in data or "synthesis" not in data:
        return {"error": "Missing data. Make sure extraction, swot, and synthesize have all been run for this run_id."}

    # Load and render the template
    with open("report_template.md", "r", encoding="utf-8") as f:
        template = Template(f.read())

    rendered = template.render(
        company_name=company_name,
        company_url=company_url,
        generated_date=datetime.now().strftime("%Y-%m-%d"),
        extraction=data["extraction"],
        swot=data["swot"],
        synthesis=data["synthesis"]
    )

    # Save the rendered report to a file
    filename = f"report_run_{run_id}.md"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(rendered)

    return {"message": "Report generated", "filename": filename, "preview": rendered[:500]}

@app.get("/reports/{run_id}/pdf")
def generate_pdf_report(run_id: int):
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    cur.execute("""
        SELECT c.name, c.url FROM companies c
        JOIN research_runs r ON r.company_id = c.id
        WHERE r.id = %s
    """, (run_id,))
    company_row = cur.fetchone()
    if not company_row:
        cur.close()
        conn.close()
        return {"error": "Run not found"}

    company_name, company_url = company_row

    cur.execute(
        "SELECT framework_name, output_json FROM framework_outputs WHERE run_id = %s",
        (run_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    data = {name: output for name, output in rows}
    if "extraction" not in data or "swot" not in data or "synthesis" not in data:
        return {"error": "Missing data. Run extraction, swot, and synthesize first."}

    extraction = data["extraction"]
    swot = data["swot"]
    synthesis = data["synthesis"]

    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    def heading(text, size=16):
        pdf.set_font("Helvetica", "B", size)
        pdf.multi_cell(0, 10, text)
        pdf.ln(2)

    def subheading(text):
        pdf.set_font("Helvetica", "B", 12)
        pdf.multi_cell(0, 8, text)

    def body(text):
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 7, text)

    def bullet(text):
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 7, f"- {text}")

    # Title
    heading(f"Product Analysis Report: {company_name}", 18)
    body(f"Website: {company_url}")
    body(f"Generated: {datetime.now().strftime('%Y-%m-%d')}")
    pdf.ln(5)

    # Overview
    heading("1. Company Overview")
    body(extraction.get("company_description", ""))
    bullet(f"Business Model: {extraction.get('business_model', '')}")
    bullet(f"Target Users: {extraction.get('target_users', '')}")
    bullet(f"Pricing: {extraction.get('pricing_info', '')}")
    bullet(f"Mission: {extraction.get('stated_mission', '')}")
    pdf.ln(3)
    subheading("Key Features")
    for f in extraction.get("key_features", []):
        bullet(f)
    pdf.ln(5)

    # SWOT
    heading("2. SWOT Analysis")
    for category in ["strengths", "weaknesses", "opportunities", "threats"]:
        subheading(category.capitalize())
        for item in swot.get(category, []):
            bullet(item)
        pdf.ln(2)

    # Feature Ideas
    heading("3. Recommended Feature Ideas")
    for item in synthesis.get("feature_ideas", []):
        subheading(item.get("idea", ""))
        body(item.get("reasoning", ""))
        pdf.ln(1)

    # Metrics
    heading("4. Metrics to Track")
    for item in synthesis.get("metrics_to_track", []):
        bullet(f"{item.get('metric', '')} -- {item.get('reasoning', '')}")

    # Experiments
    heading("5. Suggested Experiments")
    for item in synthesis.get("experiments", []):
        subheading(item.get("experiment", ""))
        body(f"Hypothesis: {item.get('hypothesis', '')}")
        pdf.ln(1)

    # Roadmap
    heading("6. Roadmap")
    roadmap = synthesis.get("roadmap", {})
    for phase in ["now", "next", "later"]:
        subheading(phase.capitalize())
        for item in roadmap.get(phase, []):
            bullet(item)
        pdf.ln(2)

    # Interview Questions
    heading("7. PM Interview Questions to Practice")
    for i, q in enumerate(synthesis.get("interview_questions", []), 1):
        body(f"{i}. {q}")

    pdf_filename = f"report_run_{run_id}.pdf"
    pdf.output(pdf_filename)

    return {"message": "PDF generated", "filename": pdf_filename}