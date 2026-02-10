# SMB Competitive Intelligence + RAG Copilot

This document outlines the product roadmap for an SMB-focused intelligence tool.
**Pivot:** We acknowledge that SMB websites are static "brochureware." We shift focus to **Social Feed Intelligence**—monitoring the active channels (Facebook, Instagram, Nextdoor) where business owners actually post updates, offers, and real-time news.

## Sales Pitch (SMB-Friendly)

**"They post it. We find it. You win."**

Your competitors are posting discounts on Facebook, showing off new projects on Instagram, and getting roasted on Nextdoor. You don't have time to scroll their feeds all day. We do.

- **Instant Battlecards:** A 1-page report comparing your static prices and services.
- **The "Social Spy":** We read their Facebook & Instagram captions to find "Hidden Offers" they never put on their website.
- **Neighborhood Watch:** We monitor Nextdoor and Local Reviews to see what actual neighbors are saying about them.
- **The "Pulse" Alert:** We only email you when it matters. "Competitor X just posted a $50 coupon," or "Competitor Y just got 3 bad reviews in a row."

## Core Value Pillars

### 1. The "One-Click Battlecard" (The Hook)
- **Problem:** Users don't know where to start.
- **Solution:** Enter a URL -> Get a "Cheat Sheet."
- **Benefit:** Immediate value. Shows the user we understand their industry structure (Price, Warranty, Service Area).

### 2. The Social Feed Parser (The "Hidden" Intel)
- **Problem:** SMBs are bad at updating websites. They post their *real* news (Hiring, Promos, Holiday Hours) on Facebook/Instagram.
- **Solution:** We treat their Social Feeds as structured data.
- **Benefit:** "I found a '10% off' promo buried in their Instagram caption from yesterday. Match it."

### 3. Reputation & "Nextdoor" Sentinel (The Attack Vector)
- **Problem:** A 4.5-star rating on Google is vague. A specific complaint on Nextdoor ("They never showed up!") is actionable intel.
- **Solution:** Aggregation of "Voice of Customer."
- **Benefit:** **Weaponized Feedback.** "Neighbors are complaining about their wait times. Launch a campaign guaranteeing 'Same-Day Service'."

---

## Roadmap

### Phase 1 – The "Deep Audit" (Days 1–14)
*Focus: Static Data + Review Baselines. Establish the "State of the Union."*

- **The Battlecard:** Standard scraping of the Competitor Website (Pricing, Services, Warranties).
- **Review Baseline:**
    - Pull last 50 Google/Facebook Reviews.
    - **Sentiment Cluster:** "Top 3 things people hate about them" vs. "Top 3 things people love."
- **Social "Pulse" Check:**
    - **Activity Score:** Are they posting daily? Weekly? Never?
    - **Platform Presence:** Do they exist on Nextdoor? Do they have an Instagram? (Identify the attack surface).

### Phase 2 – The "Social Sentinel" (Weeks 3–6)
*Focus: Parsing Facebook & Instagram Feeds for "Messaging."*

- **The "Promo Hunter":**
    - **Input:** Last 10 posts from Facebook/Instagram.
    - **AI Job:** Scan captions and images for *numbers* ($ or %).
    - **Alert:** "Competitor X posted a flyer for '$99 Tune-Ups' 4 hours ago."
- **The "Tone" Detector:**
    - **Input:** Post captions.
    - **Insight:** Are they desperate ("We have openings today!") or arrogant ("Booked out for 3 weeks")?
    - **Action:** If desperate -> "Run ads now, they are weak." If booked -> "Raise your prices, supply is low."
- **Showcase Monitor (Instagram):**
    - **Input:** Recent image descriptions.
    - **Insight:** "They are posting a lot of Commercial/Industrial work. They might be pivoting away from Residential."

### Phase 3 – The "Neighborhood Watch" (Weeks 7–12)
*Focus: Hyper-local reputation and Nextdoor integration.*

- **Nextdoor "Mention" Tracking:**
    - *Note: High technical difficulty due to login walls. Likely requires "Bring Your Own Cookie" or specialized APIs.*
    - **Goal:** Detect when someone asks "Can anyone recommend a [Service]?" in the user's zip code.
- **Review Velocity Alert:**
    - **Trigger:** Competitor gets >3 negative reviews in 1 week.
    - **Alert:** "Blood in the water. Competitor X is having a meltdown. Launch the 'Reliability' email campaign to their zip codes."
- **The "Reply" Grade:**
    - **Analysis:** Does the competitor reply to bad reviews?
    - **Score:** "They ignore complaints" (Vulnerable) vs. "They fight back" (Aggressive).

---

## Technical Assets

### The "Social Analyst" System Prompt
This prompt is designed to parse the unstructured text of social media captions (often full of emojis and hashtags) into structured Competitive Intelligence.

**Prompt:**
```text
You are an expert Competitive Intelligence Analyst.
You have been given the last 5 posts from a Competitor's Social Feed (Facebook/Instagram).

Your Goal: Extract "Winnable Intelligence" for our client.

Input Data:
[
  { "Date": "2 days ago", "Text": "🎃 Spooky savings! Get $50 off any repair this week only! #plumbing #deal" },
  { "Date": "5 days ago", "Text": "Look at this beautiful bathroom remodel we just finished. #proud" },
  { "Date": "1 week ago", "Text": "We are looking for experienced techs. $5000 signing bonus! DM us." }
]

Analyze for these 3 Signals:
1. **Hidden Offers:** Extract any specific dollar amounts ($) or discounts (%).
2. **Desperation/Hiring:** Are they throwing money at hiring? (Implies they are short-staffed = vulnerable on service times).
3. **Strategic Shift:** Are they pushing a new service they didn't offer before?

Output Format (JSON):
{
  "active_promos": ["$50 off any repair (Expires: End of week)"],
  "operational_state": "VULNERABLE - Aggressively hiring with high bonus ($5k).",
  "messaging_tone": "Promotional/Discount-heavy"
}