## 1.0.1 - 2026-03-24

### Fixes
- Enable the multi-manager runtime switches in the canonical live config so projection and control-plane defaults no longer collapse to a disabled manager architecture.
- Normalize web configuration booleans through explicit true/false token parsing and keep the web runtime state stateless by default.
- Tighten runtime contract reference validation so `text/event-stream` no longer falls through unresolved body reference checks.
- Keep `stage_snapshots` and `contract_stage_snapshots` aligned during training execution while preserving richer canonical stage payloads.

### Documentation
- Sync Commander CLI examples in the README with the live parser surface and remove the stale `strategies` command reference.
- Record the P1/P2 repair closeout and release evidence for the 2026-03-24 hotfix cycle.

### Tests
- Add regression coverage for config normalization, runtime API contract validation, stateless web runtime behavior, snapshot canonicalization, and README/structure guard enforcement.
- Wire the new release-closure checks into the freeze gate focused protocol bundle so future drift is blocked automatically.
