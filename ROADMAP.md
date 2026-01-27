# SMB Competitive Intelligence + RAG Copilot

This document outlines the product roadmap and sales strategy for an SMB-focused intelligence tool. It prioritizes proactive insights (telling the user what matters) over passive Q&A (waiting for the user to ask).

## Sales Pitch (SMB-Friendly)

**"Don't just browse your competitors. Spy on them."**

Most business owners know their competitors exist, but they don't have time to stalk their websites every day. We turn your competitors' websites into a living spreadsheet and an always-on analyst.

- **Instant Battlecards:** Get a 1-page report comparing your prices, guarantees, and services to your top rival in seconds.
- **No-Noise Alerts:** We don't bug you about typos. We only alert you when a competitor changes a price, launches a promo, or adds a service.
- **The "Truth" Engine:** Your sales team stops guessing. When a lead says "Competitor X is cheaper," you can type "Check Competitor X pricing" and get the proof instantly.
- **OSINT Intelligence:** We don't just watch their website. We track their job postings (are they hiring?), building permits (are they expanding?), customer reviews (what are people saying?), and local events (where are they marketing?).

## Core Value Pillars

### 1. The "One-Click Battlecard" (Immediate Value)

- **Problem:** Users don't know what to ask an AI.
- **Solution:** We don't wait for a prompt. As soon as the crawl finishes, we auto-generate a "Sales Battlecard" comparing the User vs. Competitor.
- **Benefit:** Immediate "Wow" factor without the user typing a single word.

### 2. Semantic Change Detection (Retention)

- **Problem:** Standard web monitors spam users with alerts for every HTML change (timestamps, footer fixes).
- **Solution:** We use LLMs to ask, "Did the meaning of this page change?"
- **Benefit:** Users trust our emails. If we say "Pricing Changed," they open it.

### 3. Competitor Database (Tangible Asset)

- **Problem:** Chatbots feel ephemeral.
- **Solution:** "Export to CSV." We turn unstructured web pages into structured rows: Service Name, Price Point, Warranty Terms.
- **Benefit:** SMB owners love spreadsheets they can touch and edit.

### 4. OSINT Intelligence Layer (Pre-Sign "Wow" Factor)

- **Problem:** Website monitoring only shows what competitors want you to see. Real competitive signals come from public records and behavior patterns.
- **Solution:** Integrate open-source intelligence feeds that reveal expansion plans, hiring trends, customer sentiment, and market activity.
- **Benefit:** Prospects see value before they even sign up. "Wait, you can tell me if my competitor is hiring? That means they're growing..."

**Core OSINT Data Sources:**

- **Job Postings:** Track competitor hiring patterns (new roles = expansion, layoffs = trouble). Sources: Indeed, LinkedIn, company career pages.
- **Building Permits:** Monitor construction/renovation permits (new location = market entry). Sources: Municipal permit databases, public records APIs.
- **Customer Reviews:** Aggregate sentiment analysis across Google, Yelp, BBB, industry-specific platforms. Track review velocity and complaint patterns.
- **Local Events:** Monitor competitor participation in trade shows, community events, sponsorships. Signals marketing strategy and market focus.

**Future Considerations (Advanced Datasets):**

- **Weather/Traffic Data:** Correlate service demand with local conditions (HVAC during heat waves, roofing after storms).
- **Census/Parcel Data:** Understand demographic shifts and property ownership patterns in target markets.
- **Note:** These require specialized APIs and may have cost/access limitations. Evaluate ROI per vertical.

## Roadmap

### Phase 1 – The "Value Shock" Demo (1–2 weeks)

- **Goal:** The user sees a comparison table of their business vs. their rival within 5 minutes of onboarding.
- **Inputs:** User URL + 1 Competitor URL.
- **Core Capabilities:**
    - Crawl, scrape, and chunk text.
    - **Auto-Trigger:** Upon crawl completion, run the "Battlecard Prompt" (see below).
    - **Output:** Display a Markdown table comparing Services, Warranties, and Pricing.
    - **OSINT Quick Win:** Pull competitor's latest 10 Google/Yelp reviews and show sentiment summary in Battlecard.
- **UI Focus:** A simple dashboard showing "Crawl Complete" -> "View Battlecard".
- **Metric:** Time to first "Aha!" moment (target: < 3 mins).

### Phase 2 – The "Retention" MVP (3–6 weeks)

- **Goal:** Give them a reason to keep the subscription after the initial curiosity fades.
- **Multi-tenant Architecture:** Secure data isolation per SMB.
- **Semantic Change Detection:**
  - Compare current crawl vs. last week.
  - LLM Filter: "Ignore styling changes. Flag only price numbers, new headers, or removed guarantees."
- **The "Spy Report":** A Friday email summary: "Competitor A changed their pricing page. They raised the price of Service X by $50."
- **Data Export:** Button to download the extracted "Services & Pricing" table as a CSV/Excel file.
- **OSINT Integration:**
  - **Job Posting Monitor:** Weekly scan of competitor career pages. Alert: "Competitor X posted 3 new job listings (2 Sales, 1 Operations)."
  - **Review Aggregation:** Monthly sentiment report comparing your reviews vs. competitors. Track review volume trends.
  - **Building Permit Watch:** (If available via public APIs) Alert on new permits filed by competitor business addresses.

### Phase 3 – Growth & Scale (6–12 weeks)

**Goal:** Vertical-specific dominance and self-service scaling.

- **Industry Templates:** Pre-built extraction schemas for specific niches.
  - **HVAC:** "Emergency Fee," "Tune-up Cost," "Warranty Years."
  - **Legal:** "Retainer Fee," "Consultation Cost," "Practice Areas."
- **Intent Tagging:** Auto-tag pages as "Pricing," "Landing Page," "Blog," or "Legal."
- **Team Access:** Allow Sales, Marketing, and Support to have different "Views" (e.g., Sales sees Battlecards; Marketing sees Copywriting styles).
- **Advanced OSINT:**
  - **Event Tracking:** Monitor competitor participation in local events, trade shows, sponsorships. Calendar view of competitor activity.
  - **Hiring Trend Analysis:** Historical job posting patterns. "Competitor X has posted 8 jobs in Q1 vs. 2 in Q4. They're scaling."
  - **Review Deep Dive:** Competitor-specific review analysis. "Top 3 complaints about Competitor Y: slow response (45%), pricing (30%), quality (25%)."
  - **OSINT Dashboard:** Dedicated tab showing all non-website intelligence in one place.

## Technical Assets

### The "One-Click Battlecard" System Prompt

This prompt is designed to run automatically once the scraping is complete. It takes the retrieved context chunks from both the User's Site and the Competitor's Site and forces a structured comparison.

**Prompt:**

```
You are an expert Competitive Intelligence Analyst for an SMB. Your job is to create a brutal, honest, and high-value "Sales Battlecard" comparing [User Company Name] against [Competitor Company Name].

Use the provided context chunks from both websites to populate the report.

Rules:
1. NO FLUFF. Be direct and concise.
2. If data is missing for a specific field, write "Not Found" rather than hallucinating.
3. Citations are mandatory. (e.g., [Source: Competitor Pricing Page]).
4. Focus on "Winnable Moments"—where does the User have an advantage?

Output Format (Markdown):

# ⚔️ Battlecard: [User Company] vs. [Competitor Company]

## 🏆 Executive Summary
(1-2 sentences on who looks stronger on paper and why. e.g., "Competitor B has lower upfront pricing, but User Company offers superior warranty terms.")

## 📊 Head-to-Head Matrix
| Feature | [User Company] | [Competitor Company] | Winner |
| :--- | :--- | :--- | :--- |
| **Core Offer** | (e.g. 24/7 HVAC Repair) | (e.g. 8am-8pm Repair) | [User] |
| **Pricing** | (e.g. $99 diagnostic) | (e.g. Free diagnostic) | [Competitor] |
| **Guarantees** | (e.g. 1 Year Labor) | (e.g. 90 Days) | [User] |
| **Social Proof** | (e.g. "Voted Best in City") | (e.g. 500+ Reviews) | [Draw] |

## 💰 Pricing & Offers Breakdown
* **[User Company]:** List specific price points or offers found.
* **[Competitor Company]:** List specific price points or offers found.
* **Analysis:** (e.g., "They undercut you on the initial visit, but your membership plan is cheaper annually.")

## 📣 Marketing Messaging Gap
* **They say:** (e.g., "Fastest response time")
* **You say:** (e.g., "Family owned since 1990")
* **Opportunity:** (e.g., "They stress speed; you stress trust. In sales calls, highlight that speed often means sloppy work.")

## ⚠️ Kill Shots (Your Advantages)
1. (Key differentiator 1 with source)
2. (Key differentiator 2 with source)
3. (Key differentiator 3 with source)
```

### OSINT Data Source Implementation

**Job Postings:**
- **Sources:** Indeed API (if available), LinkedIn company pages (scraping), company career page monitoring.
- **Frequency:** Weekly crawl of competitor career pages + Indeed search for company name.
- **Extraction:** Job title, department, location, posting date. LLM to categorize: "Sales," "Operations," "Technical," etc.
- **Alert Logic:** New postings in last 7 days = "Hiring Signal."

**Building Permits:**
- **Sources:** Municipal permit databases (varies by city), public records APIs (e.g., BuildFax, PermitFlow).
- **Frequency:** Monthly scan (permits are slow-moving).
- **Extraction:** Permit type, address, value, date filed. Match addresses to competitor business locations.
- **Alert Logic:** New permit for competitor address = "Expansion Signal."

**Customer Reviews:**
- **Sources:** Google Places API, Yelp Fusion API, BBB API, industry-specific platforms.
- **Frequency:** Weekly aggregation.
- **Extraction:** Review text, rating, date, platform. LLM sentiment analysis (positive/negative/neutral).
- **Alert Logic:** Significant sentiment shift or review volume spike.

**Local Events:**
- **Sources:** Eventbrite API, Facebook Events, local chamber of commerce sites, trade show directories.
- **Frequency:** Monthly scan.
- **Extraction:** Event name, date, location, organizer/sponsor. Match to competitor names.
- **Alert Logic:** Competitor listed as sponsor/participant = "Marketing Activity Signal."

**Implementation Priority:**
1. **Phase 1:** Reviews (easiest APIs, immediate value).
2. **Phase 2:** Job postings (scraping + API hybrid).
3. **Phase 3:** Building permits (requires city-specific integration).
4. **Phase 3+:** Events (requires fuzzy matching and data quality filtering).

## Trust and Quality Guardrails

- **Citations are Non-Negotiable:** Every claim in the Battlecard or Q&A must link back to the source URL.
- **Explicit Uncertainty:** If the scraper didn't find a price, the AI must say "Price not listed on website," not guess.
- **Privacy:** Competitor data is public, but the User's internal documents (uploaded policies, etc.) are strictly isolated.
