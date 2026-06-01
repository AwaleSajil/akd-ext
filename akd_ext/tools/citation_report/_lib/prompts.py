"""Prompt for the Citation Analyzer LLM (used only by analyze_citations).

The target profiling prompt lives in the agent layer; this package only needs the
per-paper analyzer prompt, since analyze_citations runs that LLM call itself.
"""

from __future__ import annotations

ANALYZER_PROMPT = """You are a scientific literature analyst. Read the research paper below and produce a
structured report answering one question: how does this paper use {target_name}
({target_domain})?

WHAT TO LOOK FOR
Search the full paper text for any mention or use of {target_name}, including variants:
- {target_aliases}
- Any model/method referred to as {target_name} in the context of {target_domain}
Guidance specific to this target: {what_to_look_for}

HOW TO CLASSIFY USAGE
Use exactly one primary usage_type:
- mention_only      : {target_name} only appears in intro, related work, or citations — never used directly.
- benchmark         : {target_name} is used as a baseline or comparison point in experiments.
- fine_tune         : {target_name}'s weights are updated for a downstream task.
- feature_extractor : {target_name} is used frozen as an encoder/backbone, no weight updates.
- adapted           : {target_name} is modified architecturally (new heads, adapters, etc.) but not standard fine-tuning.
- unclear           : not enough evidence to determine usage.

RULES
- Do not hallucinate. Every claim in `report` must be traceable to a quote in
  `evidence`. If something is not stated in the text, use "", [], false, or "unknown".
- Verbatim quotes only. All `quote` and `evidence_quote` fields must be copied exactly
  from the paper text — do not paraphrase.
- Populate `evidence` first as you read, then use only those quotes to fill `report`.
- If {target_name} is not mentioned at all, return evidence: [] and set usage_type to
  "unclear" with an appropriate usage_summary.
- metrics.usage_type_primary must match report.usage_type.
- evidence_used_count must equal the length of the evidence array.
- For all true/false/"unknown" fields: use true or false only when the text explicitly
  supports it; otherwise "unknown".
- For evidence_quality.confidence: rate 0.0-1.0 by how much clear evidence you found.
- page_hint: use the number from the nearest "--- page N ---" marker in the text.

Return ONLY the JSON object for the schema. No markdown, no code fences.

PAPER TEXT:
{pdf_text}"""


def build_analyzer_input(target, pdf_text: str) -> str:
    """target is a TargetSpec (has name/aliases/domain/what_to_look_for)."""
    aliases = ", ".join(target.aliases) if getattr(target, "aliases", None) else target.name
    return ANALYZER_PROMPT.format(
        target_name=target.name,
        target_domain=target.domain,
        target_aliases=aliases,
        what_to_look_for=target.what_to_look_for,
        pdf_text=pdf_text,
    )
