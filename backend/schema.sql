CREATE TABLE companies (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE research_runs (
    id SERIAL PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id),
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE raw_sources (
    id SERIAL PRIMARY KEY,
    run_id INTEGER REFERENCES research_runs(id),
    source_type TEXT NOT NULL,
    source_url TEXT,
    raw_text TEXT,
    scraped_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE framework_outputs (
    id SERIAL PRIMARY KEY,
    run_id INTEGER REFERENCES research_runs(id),
    framework_name TEXT NOT NULL,
    output_json JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);