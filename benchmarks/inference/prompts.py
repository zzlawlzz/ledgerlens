"""Inference benchmark prompt suite (T-037 §1).

Twenty prompts spanning the four LLM task classes the orchestrator actually
routes (see ``config/router.yaml`` policy): ``route`` (fast classification),
``extract`` (pull a structured field), ``plan`` (decompose a question into
steps), ``synthesize`` (write a grounded answer from retrieved context).

Five prompts per class keeps the suite balanced and cheap to run against a
paid API while still exercising short-and-cheap vs. longer-and-reasoned
generation. Each prompt bounds its own output length in the system message so
tokens/sec stays a meaningful, comparable number across models rather than a
function of how verbose one model happens to be.

The prompts are self-contained (no DB/RAG dependency) so the suite reproduces
identically on any node — the point is to measure the *models*, not retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BenchPrompt:
    id: str
    task_class: str  # route | extract | plan | synthesize
    system: str
    user: str


# --------------------------------------------------------------------------- #
# route — classify a user question; single-token-ish answer, latency-bound
# --------------------------------------------------------------------------- #

_ROUTE_SYSTEM = (
    "You are a router. Classify the user's finance question into exactly one "
    "label: NUMERIC (needs a figure from filings), NARRATIVE (needs 10-K prose "
    "such as risks/strategy), or NODATA (out of scope / not answerable from "
    "SEC filings). Reply with the single label word only."
)

_ROUTE = [
    ("route_1", "What was Apple's total revenue in fiscal 2023?"),
    ("route_2", "What supply-chain risks does NVIDIA disclose in its latest 10-K?"),
    ("route_3", "Should I buy Tesla stock next week?"),
    ("route_4", "How did Microsoft describe its cloud strategy?"),
    ("route_5", "What is Amazon's operating margin trend over three years?"),
]

# --------------------------------------------------------------------------- #
# extract — pull one structured value out of a snippet; short, deterministic
# --------------------------------------------------------------------------- #

_EXTRACT_SYSTEM = (
    "Extract the single requested value from the text. Reply with only the "
    "value (a number with its unit, or a date), no prose, no explanation."
)

_EXTRACT = [
    (
        "extract_1",
        "From: 'Net income for fiscal 2023 was $96,995 million, up from "
        "$99,803 million in 2022.' Extract fiscal 2023 net income.",
    ),
    (
        "extract_2",
        "From: 'The company repurchased 471 million shares for $76.6 billion "
        "during the year.' Extract the total repurchase amount.",
    ),
    (
        "extract_3",
        "From: 'Our fiscal year ends on the last Saturday of September; fiscal "
        "2023 ended September 30, 2023.' Extract the fiscal 2023 end date.",
    ),
    (
        "extract_4",
        "From: 'Research and development expense rose to $29,915 million, or "
        "about 7.8% of net sales.' Extract R&D as a percent of net sales.",
    ),
    (
        "extract_5",
        "From: 'Total assets were $352.6 billion and total liabilities were "
        "$290.4 billion at year end.' Extract total liabilities.",
    ),
]

# --------------------------------------------------------------------------- #
# plan — decompose a question into tool steps; medium reasoning
# --------------------------------------------------------------------------- #

_PLAN_SYSTEM = (
    "You are a planner for a financial-analysis agent with two tools: "
    "sql_query (structured XBRL facts) and rag_search (10-K narrative text). "
    "Given a question, output a numbered plan of at most 3 concrete steps, "
    "each naming the tool to use. No preamble."
)

_PLAN = [
    ("plan_1", "Compare Apple and Microsoft revenue growth over the last three fiscal years."),
    (
        "plan_2",
        "What are the main regulatory risks Alphabet faces, and how large is its ad revenue?",
    ),
    ("plan_3", "Summarize Tesla's production ramp and quantify its vehicle deliveries trend."),
    ("plan_4", "How concentrated is NVIDIA's revenue by segment, and what risks does it cite?"),
    (
        "plan_5",
        "Assess whether Amazon's operating margin improved and what management attributes it to.",
    ),
]

# --------------------------------------------------------------------------- #
# synthesize — write a grounded answer from provided context; quality-bound
# --------------------------------------------------------------------------- #

_SYNTH_SYSTEM = (
    "Answer the question using ONLY the numbered CONTEXT passages. Cite the "
    "passages you use as [1], [2]. If the context does not contain the answer, "
    "say so. Keep the answer to at most two sentences."
)

_SYNTH = [
    (
        "synth_1",
        "CONTEXT:\n[1] Apple's total net sales were $383.3 billion in fiscal 2023, "
        "down 3% from $394.3 billion in 2022.\n[2] Services revenue grew 9% to a "
        "record $85.2 billion.\nQUESTION: How did Apple's fiscal 2023 revenue "
        "change, and what offset part of the decline?",
    ),
    (
        "synth_2",
        "CONTEXT:\n[1] NVIDIA states that a significant portion of its revenue "
        "comes from a limited number of customers.\n[2] It warns that export "
        "controls on advanced chips to China could materially reduce sales.\n"
        "QUESTION: What two revenue risks does NVIDIA highlight?",
    ),
    (
        "synth_3",
        "CONTEXT:\n[1] Microsoft's Intelligent Cloud segment revenue was $87.9 "
        "billion, up 20%.\n[2] Azure and other cloud services grew 29%.\n"
        "QUESTION: How did Microsoft's cloud business perform?",
    ),
    (
        "synth_4",
        "CONTEXT:\n[1] The filing discusses competition in retail and "
        "advertising.\nQUESTION: What was the company's net income last year?",
    ),
    (
        "synth_5",
        "CONTEXT:\n[1] Tesla delivered 1.81 million vehicles in 2023, up 38% "
        "from 1.31 million in 2022.\n[2] Production was 1.85 million vehicles.\n"
        "QUESTION: How many vehicles did Tesla deliver in 2023 and how did that "
        "change year over year?",
    ),
]


def _build() -> list[BenchPrompt]:
    out: list[BenchPrompt] = []
    for pid, user in _ROUTE:
        out.append(BenchPrompt(pid, "route", _ROUTE_SYSTEM, user))
    for pid, user in _EXTRACT:
        out.append(BenchPrompt(pid, "extract", _EXTRACT_SYSTEM, user))
    for pid, user in _PLAN:
        out.append(BenchPrompt(pid, "plan", _PLAN_SYSTEM, user))
    for pid, user in _SYNTH:
        out.append(BenchPrompt(pid, "synthesize", _SYNTH_SYSTEM, user))
    return out


PROMPTS: list[BenchPrompt] = _build()
TASK_CLASSES: tuple[str, ...] = ("route", "extract", "plan", "synthesize")


# Per-task-class quality rubric handed to the judge (T-037 §1: "quality —
# judge score by rubric"). Kept terse so judging is cheap.
RUBRIC: dict[str, str] = {
    "route": "Correct single label (NUMERIC/NARRATIVE/NODATA) for the question, nothing else.",
    "extract": "Exactly the requested value with its unit/format, no extra prose.",
    "plan": "A short, ordered, tool-grounded plan (<=3 steps) that would answer the question.",
    "synthesize": (
        "Faithful to the CONTEXT, cites passages, <=2 sentences, admits gaps "
        "when context lacks the answer."
    ),
}
