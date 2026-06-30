# Product Analysis Report: {{ company_name }}

**Website:** {{ company_url }}
**Generated:** {{ generated_date }}

---

## 1. Company Overview

{{ extraction.company_description }}

- **Business Model:** {{ extraction.business_model }}
- **Target Users:** {{ extraction.target_users }}
- **Pricing:** {{ extraction.pricing_info }}
- **Mission:** {{ extraction.stated_mission }}

### Key Features
{% for feature in extraction.key_features %}
- {{ feature }}
{% endfor %}

---

## 2. SWOT Analysis

### Strengths
{% for item in swot.strengths %}
- {{ item }}
{% endfor %}

### Weaknesses
{% for item in swot.weaknesses %}
- {{ item }}
{% endfor %}

### Opportunities
{% for item in swot.opportunities %}
- {{ item }}
{% endfor %}

### Threats
{% for item in swot.threats %}
- {{ item }}
{% endfor %}

---

## 3. Recommended Feature Ideas

{% for item in synthesis.feature_ideas %}
**{{ item.idea }}**
{{ item.reasoning }}

{% endfor %}

---

## 4. Metrics to Track

{% for item in synthesis.metrics_to_track %}
- **{{ item.metric }}** — {{ item.reasoning }}
{% endfor %}

---

## 5. Suggested Experiments

{% for item in synthesis.experiments %}
**{{ item.experiment }}**
Hypothesis: {{ item.hypothesis }}

{% endfor %}

---

## 6. Roadmap

**Now:**
{% for item in synthesis.roadmap.now %}
- {{ item }}
{% endfor %}

**Next:**
{% for item in synthesis.roadmap.next %}
- {{ item }}
{% endfor %}

**Later:**
{% for item in synthesis.roadmap.later %}
- {{ item }}
{% endfor %}

---

## 7. PM Interview Questions to Practice

{% for q in synthesis.interview_questions %}
{{ loop.index }}. {{ q }}
{% endfor %}