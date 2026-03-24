# Contract Boundary Rollout Checklist

This checklist tracks the implementation order for tightening the training contract chain from
draft inputs toward normalized, persisted contracts.

## Phase 1. Similar Results Compact Contract

- Status: completed
- Goal: replace history-clone style `similar_results` payloads with a strict compact contract.
- Done when:
  - `review.py` emits compact similar-result items.
  - `review_contracts` owns the canonical `SimilarResultCompactPayload`.
  - persistence previews only project compact fields instead of wide history blobs.

## Phase 2. Internal Review Result Normalization

- Status: completed
- Goal: stop passing loose review dicts across internal review logic and normalize them once.
- Done when:
  - review-stage internals operate on a normalized internal object.
  - outward serialization happens only at the boundary.
  - similarity/diagnosis helpers stop depending on mixed top-level plus metadata copies.

## Phase 3. Review Report Compact Digests

- Status: completed
- Goal: keep full manager/allocation reports only at artifact-generation edges and use digests
  everywhere else.
- Done when:
  - run-context, validation payloads, stage snapshots, and persistence all carry digest shapes.
  - tests assert digest summaries instead of full `reports` payloads.
  - full reports remain only in explicit artifact recorders/builders.

## Phase 4. Optimization Event Stage Contracts

- Status: completed
- Goal: split optimization-event boundary fields into stage-specific payload contracts and make
  downstream consumers prefer those typed payloads.
- Done when:
  - runtime mutation consumers read `runtime_config_mutation_payload` or
    `runtime_config_mutation_skipped_payload` first.
  - scoring summaries derive from typed stage payloads before falling back to legacy
    `applied_change`.
  - event/test constructors preserve the stage-specific payloads.

## Phase 5. Final Verification

- Status: completed
- Goal: pass the repo verification gates after the rollout.
- Verified checks:
  - `uv run pyright`
  - `uv run ruff check src tests`
  - `uv run pytest -q`
