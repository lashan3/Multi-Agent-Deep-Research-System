# Example queries

The Deep Research Agent handles five kinds of research questions. The model
detects the type automatically (Phase 0 — Query Understanding) and adapts the
plan, the brain prompt, and the report structure accordingly.

This page is a cheat-sheet of well-shaped queries and the kind of report each
produces.

---

## Factual

A specific, verifiable answer. Reports are short and lead with the answer.

```
What is the current EU AI Act compliance deadline for general-purpose AI providers?
```

```
How many active AI startups are headquartered in the United Kingdom as of 2025?
```

```
What did the EU AI Act classify as "high risk" AI systems?
```

---

## Comparative

Two or more options side by side. Reports lead with a comparison table or
chart and then drill into each option.

```
Compare Snowflake vs Databricks for a mid-size financial services company —
features, pricing, ecosystem, and recent product moves.
```

```
What are the differences between ISO 27001 and SOC 2 for cloud vendors,
and which one matters more for a B2B SaaS selling to US enterprises?
```

```
Compare GPT-4o, Claude Sonnet, and Gemini 2.5 Pro for production coding work
in 2024–2025: benchmark scores, real-world developer surveys, pricing,
latency, and tool/function-calling reliability.
```

---

## Analytical

Why something is happening, or what it implies. Reports build a structured
argument from the evidence.

```
Why are European technology companies struggling to compete with US and
Asian peers in commercial AI? What are the structural causes vs. cyclical ones?
```

```
What are the key success factors for AI implementations in retail,
based on case studies from 2023–2025?
```

```
What caused the 2024 correction in private credit markets, and what are
the implications for institutional allocators?
```

---

## Trend

How something has changed over time. Reports lead with a timeline or line
chart and analyse the inflection points.

```
How has enterprise public-cloud spending evolved between 2020 and 2025?
Include sector breakdown and the impact of AI workloads.
```

```
Track the evolution of generative AI adoption in financial services since 2022.
```

```
How have GDPR enforcement actions and fine sizes changed year on year
since enforcement began?
```

---

## Open-ended

Broad exploration of an unfamiliar topic. The agent will likely ask
clarifying questions before starting.

```
What are the most significant developments in quantum computing in 2024–2025?
```

```
Give me a comprehensive overview of the sustainable aviation fuel market.
```

```
What is happening in the digital identity space right now?
```

---

## Tips for better reports

**Be specific about scope.**
The more you define the boundaries, the more focused the output.

```
Less focused: Research renewable energy.
More focused: What is the current state of offshore wind development in the
              North Sea, focusing on projects commissioned or under
              construction in 2023–2025?
```

**Specify the time frame.**

```
What are the latest (2024–2025) figures for AI investment in Southeast Asia?
```

**Name the entities you want covered.**

```
Compare the sustainability reporting requirements under CSRD (EU),
SEC climate disclosure rules (US), and TCFD (global).
```

**Ask for a specific output format.**

```
Give me a structured comparison table of the top 5 enterprise AI platforms.
```

```
I need a timeline of major AI regulatory milestones in the EU since 2020.
```

**Use the clarification step to your advantage.**
If the agent asks clarifying questions, treat it as an opportunity to narrow
the scope. The more precisely you answer, the more targeted the research will be.
