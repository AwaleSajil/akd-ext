"""Pydantic schemas for the LLM analysis step (used only by analyze_citations).

- TargetSpec  : the target profile {name, aliases, domain, what_to_look_for} produced by
                the agent layer and passed into analyze_citations.
- AgentOutput : the structured per-paper report the Citation Analyzer LLM must emit
                ({evidence, report}). Used as the OpenAI structured-output (response) type.

Kept self-contained here so the citation_report tools have no cross-package dependency
on the standalone agent app.
"""

from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field

# true | false | "unknown"
BoolUnknown = Union[bool, Literal["unknown"]]
UsageType = Literal[
    "mention_only", "benchmark", "fine_tune", "feature_extractor", "adapted", "unclear"
]


# ── target_spec (input to the analyzer) ──────────────────────────────────────────

class TargetSpec(BaseModel):
    name: str = Field(description="Canonical name of the model/method/tool the seed paper introduces.")
    aliases: list[str] = Field(description="All spellings/abbreviations/version names a citing paper might use, including the bare name.")
    domain: str = Field(description="Short phrase for the field/modality, e.g. 'geospatial foundation model'.")
    what_to_look_for: str = Field(description="2-5 sentences guiding the analyzer on how to detect and classify usage of this target.")


# ── evidence + report (output of the analyzer) ───────────────────────────────────

class EvidenceItem(BaseModel):
    quote: str = Field(description="Verbatim snippet copied from the paper mentioning the target.")
    page_hint: int = Field(description="Page number from the nearest --- page N --- marker.")
    reason: str = Field(description="Why this quote is relevant.")
    type_hint: Literal[
        "mention", "methods", "training", "benchmark", "result", "table", "figure",
        "citation_only", "other",
    ] = Field(description="Coarse category of the quote's role in the paper.")


class FineTuning(BaseModel):
    present: bool = Field(description="Whether fine-tuning of the target is described.")
    downstream_task: str = Field(description="Task the target was fine-tuned for.")
    dataset: str = Field(description="Dataset used for fine-tuning.")
    method: str = Field(description="Fine-tuning method (e.g. 'full fine-tune', 'LoRA').")
    improvements: list[str] = Field(description="Reported improvements from fine-tuning.")


class BenchmarkItem(BaseModel):
    task: str
    dataset: str
    metric: str = Field(description="e.g. 'IoU', 'F1'.")
    result: str = Field(description="Reported value, e.g. '0.87'.")
    evidence_quotes: list[str]


class ImpactfulStatement(BaseModel):
    statement: str = Field(description="A notable claim or finding about the target.")
    evidence_quote: str = Field(description="Verbatim quote backing it.")


class TargetPresence(BaseModel):
    mentioned: bool
    where_mentioned: list[Literal["intro", "related_work", "methods", "experiments", "appendix", "unknown"]]
    mention_count_estimate: int


class TrainingInteraction(BaseModel):
    used_pretrained_only: BoolUnknown = Field(description="Target used as-is, no weight updates.")
    fine_tuned: BoolUnknown = Field(description="Any fine-tuning applied.")
    linear_probe: BoolUnknown = Field(description="Only head/linear layer trained on frozen target.")
    lora_adapter: BoolUnknown = Field(description="LoRA/adapter tuning used.")
    full_finetune: BoolUnknown = Field(description="All target weights updated.")
    prompting_only: BoolUnknown = Field(description="Used via prompting only.")
    training_details_present: BoolUnknown = Field(description="Enough detail to determine setup.")


class Evaluation(BaseModel):
    benchmarked_against_target: BoolUnknown = Field(description="Another model compared against the target as baseline.")
    target_compared_to_others: BoolUnknown = Field(description="Target compared against other models.")
    metrics_reported: BoolUnknown = Field(description="Quantitative metrics reported for the target.")
    tables_figures_referenced: BoolUnknown = Field(description="Tables/figures with target results present.")


class DownstreamTask(BaseModel):
    task: str
    dataset: str
    modality: str = Field(description="e.g. 'multispectral'.")
    notes: str


class ImprovementValue(BaseModel):
    metric: str
    delta: str = Field(description="e.g. '+4.2%'.")
    direction: Literal["up", "down", "unknown"]
    baseline_name: str
    comparator_name: str
    evidence_quote: str


class ResultsSignal(BaseModel):
    reported_improvement: Literal["yes", "no", "unknown"]
    best_numbers_present: BoolUnknown
    improvement_values: list[ImprovementValue]


class EvidenceQuality(BaseModel):
    evidence_quote_count: int
    confidence: float = Field(ge=0.0, le=1.0)
    notes: list[str]


class Metrics(BaseModel):
    schema_version: str = Field(description="Set to 'metrics-v1'.")
    usage_type_primary: UsageType
    usage_types_secondary: list[UsageType] = Field(description="Additional usage types if the target is used in multiple ways.")
    target_presence: TargetPresence
    training_interaction: TrainingInteraction
    evaluation: Evaluation
    downstream_tasks: list[DownstreamTask]
    results_signal: ResultsSignal
    evidence_quality: EvidenceQuality


class Report(BaseModel):
    usage_type: UsageType = Field(description="Top-level classification. Must match metrics.usage_type_primary.")
    usage_summary: str = Field(description="2-6 sentence summary of how the target is used.")
    fine_tuning: FineTuning
    benchmarks: list[BenchmarkItem]
    impactful_statements: list[ImpactfulStatement]
    evidence_used_count: int = Field(description="Number of evidence quotes used. Must equal len(evidence).")
    notes: list[str] = Field(description="Caveats about missing data / extraction limits.")
    metrics: Metrics


class AgentOutput(BaseModel):
    evidence: list[EvidenceItem]
    report: Report
