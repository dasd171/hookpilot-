# konnex-adapter (Python)

- `MockKonnexAdapter` — **SIMULATION**: local file-backed job store implementing the proposed task
  `detect_bite_and_execute_hook_set` (submit → attach → execute/ingest → record bundle → prove (local
  receipt) → verify (local criteria)). Nothing is sent anywhere.
- `build_bundle` / `read_bundle` — "powp-style" zip in **our own layout** `hookpilot-evidence-bundle-v0`
  (official PoPW layout unpublished).
- `verify_evidence` / `verify_bundle` — 11 explicit criteria (C1–C11), verdict accept/reject, trust note.
- `KonnexCliAdapter` — **UNVERIFIED skeleton** of the documented CLI shapes (`robot attach`,
  `robot record`, `prove`); refuses unless `HOOKPILOT_ENABLE_REAL_KONNEX=1` and a `konnex` binary exist;
  dry-run by default; undocumented calls raise `NotImplementedError`.

```bash
python -m konnex_adapter demo --scenario bite --seed 1 --runs 3 --store ./.mock-konnex
python -m konnex_adapter verify-bundle path/to/powp.zip [--json]
python -m konnex_adapter cli-argv
```
Details and trust limits: `docs/konnex-integration.md`.
