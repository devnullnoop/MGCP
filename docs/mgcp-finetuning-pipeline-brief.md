# MGCP Phase 7: Fine-Tuning Data Pipeline

## Project Brief

You are working on MGCP (Memory Graph Control Protocol), a graph-based persistent memory system for LLM coding assistants. The codebase is Python, uses SQLite for structured data, Qdrant for vector search (with sentence-transformers for embeddings), and NetworkX for graph traversal. It exposes 32 MCP tools and stores lessons, project context, workflows, and catalogue items.

GitHub: https://github.com/devnullnoop/MGCP

## Branch Goal

Create a new feature branch (`feature/finetune-pipeline`) that extends MGCP with the ability to export its accumulated knowledge — lessons, refinement histories, usage telemetry, and workflow patterns — as structured fine-tuning datasets compatible with SFT, DPO, and RLHF training pipelines.

MGCP already accumulates exactly the kind of structured operational knowledge that fine-tuning requires. This branch transforms MGCP from a runtime memory system into a **curriculum generator** for model improvement.

---

## Architectural Context

### Why This Works

MGCP lessons already contain implicit training signal:

1. **Lessons are behavioral specifications.** Each lesson has a trigger (input context) and an action (desired output). This maps directly to SFT prompt/completion pairs once expanded into full demonstrations.

2. **Refinement histories encode preferences.** A lesson refined from v1 → v2 → v3 implicitly says "v3 > v2 > v1 for the same context." This is the exact structure DPO (Direct Preference Optimization) consumes as chosen/rejected pairs.

3. **Usage telemetry provides reward signal.** Lessons retrieved frequently and never refined are high-quality. Lessons retrieved then immediately refined indicate a bad response that was corrected. This is a weak but usable reward signal for RLHF reward model training.

4. **Workflow gates encode quality criteria.** Workflow steps with quality gates define what "correct completion" looks like for multi-step tasks — useful for training models on process adherence.

### What This Branch Does NOT Do

- Does NOT fine-tune models. It produces datasets.
- Does NOT require GPU infrastructure. It's a data pipeline.
- Does NOT replace MGCP's runtime memory function. It's an additional export capability.
- Does NOT generate synthetic training pairs (yet — that's a follow-on). This branch focuses on extracting and structuring the raw signal MGCP already has.

---

## Implementation Plan

### Module 1: Data Extraction Layer

**Location:** `src/mgcp/finetune/extractors.py`

Build extractors that pull structured data from MGCP's existing storage:

```
class LessonExtractor:
    """Extract lessons with full refinement history from SQLite."""
    
    def extract_all() -> List[LessonRecord]
    def extract_by_category(category: str) -> List[LessonRecord]
    def extract_refined_only() -> List[LessonRecord]  # Only lessons with 2+ versions
    
class UsageTelemetryExtractor:
    """Extract lesson retrieval patterns and session data."""
    
    def extract_retrieval_frequency() -> Dict[lesson_id, RetrievalStats]
    def extract_retrieval_context() -> List[RetrievalEvent]  # What query triggered each retrieval
    def extract_refinement_events() -> List[RefinementEvent]  # When/why lessons were refined
    
class WorkflowExtractor:
    """Extract workflow definitions with gate criteria."""
    
    def extract_workflows() -> List[WorkflowRecord]
    def extract_step_lesson_links() -> List[StepLessonLink]  # Which lessons surface at which steps

class GraphExtractor:
    """Extract relationship data from NetworkX graph."""
    
    def extract_relationships() -> List[Relationship]  # prerequisite, alternative, complements, specializes
    def extract_clusters() -> List[LessonCluster]  # Groups of strongly connected lessons
```

**Data model for LessonRecord:**

```python
@dataclass
class LessonVersion:
    version: int
    content: str           # The lesson text at this version
    trigger: str           # "When X happens..."
    action: str            # "Do Y..."
    timestamp: datetime
    refinement_reason: Optional[str]  # Why was the previous version insufficient?

@dataclass
class LessonRecord:
    id: str
    category: str
    tags: List[str]
    versions: List[LessonVersion]  # Ordered v1 → vN
    relationships: List[Relationship]
    retrieval_count: int
    retrieval_contexts: List[str]  # The queries that caused this lesson to surface
    was_refined_after_retrieval: bool  # Key signal: retrieved then immediately refined = failure
```

### Module 2: Format Transformers

**Location:** `src/mgcp/finetune/transformers.py`

Transform extracted data into standard fine-tuning formats.

#### SFT Format (Supervised Fine-Tuning)

Each lesson becomes a prompt/completion pair. The trigger provides context, the action provides the expected behavior.

```python
class SFTTransformer:
    """Transform lessons into prompt/completion pairs for supervised fine-tuning."""
    
    def transform(self, lessons: List[LessonRecord]) -> List[SFTExample]:
        """
        For each lesson, produce:
        {
            "messages": [
                {"role": "system", "content": "<system context about the project/domain>"},
                {"role": "user", "content": "<reconstructed trigger scenario>"},
                {"role": "assistant", "content": "<the lesson's prescribed action>"}
            ]
        }
        
        Use the LATEST version of each lesson as the completion.
        Use retrieval_contexts to inform realistic user message framing.
        """
```

**Key design decision:** The raw lesson trigger ("When committing code...") is too terse for SFT. The transformer should use the `retrieval_contexts` (the actual queries that surfaced this lesson) to reconstruct realistic user messages. This grounds the training data in real usage patterns rather than abstract rules.

#### DPO Format (Direct Preference Optimization)

Refinement histories become preference pairs. Each refinement says "the new version is preferred over the old version for this context."

```python
class DPOTransformer:
    """Transform lesson refinement histories into preference pairs."""
    
    def transform(self, lessons: List[LessonRecord]) -> List[DPOExample]:
        """
        For each lesson with 2+ versions, produce:
        {
            "prompt": "<the trigger/context>",
            "chosen": "<version N content — the latest, best version>",
            "rejected": "<version N-1 content — the version that was deemed insufficient>"
        }
        
        A lesson refined 3 times produces 2 preference pairs:
          (v3 chosen, v2 rejected) and (v2 chosen, v1 rejected)
        
        Include the refinement_reason as metadata — it explains WHY 
        the chosen response is better, which is useful for reward model 
        training annotations.
        """
```

**Key insight:** The `refinement_reason` field is gold. When a lesson says "FAILED. Added a pytest marker before imports, causing a lint error. Pushed without running the linter." — that's a natural language explanation of why the rejected version was bad. This is exactly what RLHF annotators write, but generated organically from real failures.

#### RLHF Reward Signal Format

Usage telemetry becomes a weak reward signal dataset.

```python
class RewardSignalTransformer:
    """Transform usage telemetry into reward training signal."""
    
    def transform(self, lessons: List[LessonRecord]) -> List[RewardExample]:
        """
        Score each lesson version based on:
        
        - retrieval_count (higher = more useful, but normalize by age)
        - was_refined_after_retrieval (True = penalize, the lesson failed in practice)
        - version_stability (lessons that haven't been refined in N sessions = high confidence)
        - relationship_density (lessons with many connections = foundational knowledge)
        
        Output:
        {
            "prompt": "<trigger context>",
            "response": "<lesson action>",
            "reward_signals": {
                "retrieval_frequency": float,    # 0-1 normalized
                "stability_score": float,         # 0-1, based on time since last refinement
                "failure_penalty": float,          # negative if refined after retrieval
                "graph_centrality": float          # from NetworkX PageRank or betweenness
            },
            "composite_score": float              # weighted combination
        }
        """
```

### Module 3: Export Engine

**Location:** `src/mgcp/finetune/exporter.py`

Orchestrates extraction → transformation → output in standard formats.

```python
class FinetuneExporter:
    """Main export engine. Coordinates extractors and transformers."""
    
    def export_sft(self, output_path: str, format: str = "jsonl") -> ExportReport
    def export_dpo(self, output_path: str, format: str = "jsonl") -> ExportReport  
    def export_reward(self, output_path: str, format: str = "jsonl") -> ExportReport
    def export_all(self, output_dir: str) -> ExportReport
    
    def export_huggingface(self, output_dir: str) -> ExportReport:
        """Export in HuggingFace datasets format with dataset card."""
    
    def generate_report(self) -> DatasetReport:
        """
        Produce a quality report:
        - Total examples per format
        - Category distribution
        - Preference pair count and average version depth
        - Reward signal distribution histogram
        - Coverage gaps (categories with few lessons)
        - Staleness warnings (lessons never retrieved)
        """
```

**Output formats to support:**

1. **JSONL** — Standard for most fine-tuning frameworks (OpenAI, Anthropic, Axolotl, TRL)
2. **HuggingFace Datasets** — Arrow format with dataset card for direct upload
3. **Alpaca format** — `instruction/input/output` triples for compatibility with older tooling

### Module 4: CLI Commands

**Location:** Extend existing CLI pattern in MGCP.

```bash
# Export all formats
mgcp-finetune export --output-dir ./finetune-data

# Export specific format
mgcp-finetune export --format sft --output ./sft-data.jsonl
mgcp-finetune export --format dpo --output ./dpo-pairs.jsonl
mgcp-finetune export --format reward --output ./reward-signal.jsonl

# Quality report without export
mgcp-finetune report

# Export only lessons from specific categories
mgcp-finetune export --format sft --category "git-operations" --output ./git-sft.jsonl

# Export with minimum quality threshold
mgcp-finetune export --format dpo --min-versions 3 --output ./high-quality-dpo.jsonl

# HuggingFace dataset export with card
mgcp-finetune export --format huggingface --output-dir ./hf-dataset --dataset-name "mgcp-coding-lessons"
```

### Module 5: MCP Tools (Optional, Phase 2)

Expose fine-tuning export as MCP tools so the LLM itself can trigger exports:

```
finetune_export_sft       — Export SFT dataset from current lessons
finetune_export_dpo       — Export DPO preference pairs from refinement history  
finetune_quality_report   — Generate dataset quality assessment
finetune_suggest_gaps     — Identify areas where more lessons would improve training data
```

The `finetune_suggest_gaps` tool is particularly interesting: it analyzes the current lesson corpus and identifies categories or workflow steps with low coverage, then suggests what kinds of lessons would most improve the fine-tuning dataset. This closes the loop — the LLM helps improve its own future training data.

---

## File Structure

```
src/mgcp/finetune/
├── __init__.py
├── extractors.py          # Module 1: Data extraction from MGCP storage
├── transformers.py        # Module 2: Format transformation (SFT, DPO, Reward)
├── exporter.py            # Module 3: Export orchestration and reporting
├── models.py              # Shared data models (LessonRecord, SFTExample, DPOExample, etc.)
├── scoring.py             # Reward signal computation logic
└── cli.py                 # Module 4: CLI entry points

tests/
├── test_extractors.py
├── test_transformers.py
├── test_exporter.py
├── test_scoring.py
└── fixtures/
    └── sample_lessons.json  # Test fixtures with multi-version lessons
```

---

## Implementation Order

1. **`models.py`** — Define all data classes first. Get the data model right before writing any logic.
2. **`extractors.py`** — Start with `LessonExtractor` since it touches existing SQLite schema. Verify you can pull lesson versions and refinement history correctly. This will reveal any schema gaps.
3. **`scoring.py`** — Implement reward signal computation. Depends on extractors for telemetry data.
4. **`transformers.py`** — SFT transformer first (simplest), then DPO, then Reward. Each builds on the previous.
5. **`exporter.py`** — Orchestration layer. Wire up extractors → transformers → file output.
6. **`cli.py`** — CLI entry points. Follow existing MGCP CLI patterns (`mgcp-export`, `mgcp-import`).
7. **Tests** — Write tests alongside each module. Use fixture data that represents realistic lesson progression (v1 suggestion → v2 failure documentation → v3 enforced gate).

---

## Critical Design Decisions to Make Early

### 1. Schema Discovery

Before writing extractors, audit the current SQLite schema to understand:
- How are lesson versions stored? Is there a version history table, or does `refine_lesson` overwrite?
- What telemetry is captured on retrieval? Timestamp? Query text? Session ID?
- Are refinement reasons stored as structured data or embedded in lesson text?

**If version history is not currently stored**, this branch will need a migration to add a `lesson_versions` table. This is the highest-risk item — do this first.

### 2. Retrieval Context Preservation

MGCP's `query_lessons` tool executes semantic search. The query text that triggered a retrieval is valuable training signal (it tells you what real user prompts look like when this lesson is relevant). If this isn't currently logged, add telemetry to capture it.

### 3. Composite Reward Scoring Weights

The reward signal transformer needs weights for combining retrieval frequency, stability, failure penalty, and graph centrality. Start with equal weights and expose them as configuration:

```yaml
# ~/.mgcp/finetune-config.yaml
reward_weights:
  retrieval_frequency: 0.3
  stability_score: 0.3
  failure_penalty: 0.25
  graph_centrality: 0.15
```

### 4. Minimum Viable Export

For the first working version, focus on JSONL output of SFT pairs. This is the simplest format, the most widely supported, and immediately useful for LoRA fine-tuning experiments. DPO and reward signal exports are higher value but can follow.

---

## Future Extensions (Not This Branch)

These are explicitly out of scope but should inform design decisions:

- **Synthetic data generation:** Use a strong model to expand terse lessons into full conversation demonstrations. The current branch provides the *specifications*, a future branch generates *demonstrations*.
- **Feedback loop integration:** After fine-tuning, MGCP validates the tuned model's outputs against the same lessons and flags regressions. New failures become new lessons, which become new training data.
- **Cross-project aggregation:** Merge lesson corpora from multiple MGCP instances into a unified training dataset. Requires deduplication and conflict resolution.
- **Domain-specific dataset cards:** Auto-generate HuggingFace dataset cards with provenance, license, and intended use documentation.

---

## Success Criteria

1. `mgcp-finetune export --format sft` produces valid JSONL loadable by HuggingFace `datasets` library
2. `mgcp-finetune export --format dpo` produces preference pairs from lessons with 2+ versions
3. `mgcp-finetune report` generates a quality assessment with actionable coverage gaps
4. All existing MGCP tests continue to pass (no regressions)
5. New module has >80% test coverage
6. Documentation added to `docs/` explaining the pipeline and format specifications
