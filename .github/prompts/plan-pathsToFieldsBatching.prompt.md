Plan: Batch apply & prefetch for paths→fields

Summary

Reduce per-entry DB/API round-trips by batching reads, pre-resolving tag names,
pre-ensuring value types, and applying updates in chunks. This keeps behavior
identical while lowering Python/DB overhead for large libraries.

Implementation Steps

1. Batch entry fetch
- Change the apply loop to fetch entries in chunks using
  library.get_entries_full(entry_ids) and adapt the iterator to operate on
  batches instead of single entries.

2. Pre-resolve tags
- Collect all distinct tag name tokens from previews and resolve them once
  via library.get_tag_by_name, building a name→id cache used during apply.

3. Pre-create value types
- Collect distinct field keys from all previews and call ensure/create value
  types in one pass before per-entry writes to avoid repeated ensure calls.

4. Batch apply updates
- Accumulate updates per batch. Group tag additions by tag_id, then call
  library.add_tags_to_entries with multiple entry_ids per tag_id. For field
  writes, either reuse library.update_entry_field(entry_ids, ...) where
  applicable or implement a bulk add_fields_to_entries library API to insert
  many field rows in one transaction.

5. Progress & cancellation
- Emit progress at chunk granularity, check cancellation between chunks, and
  ensure ongoing DB transactions are rolled back on cancel.

6. Tests & benchmarks
- Add tests using a large synthetic library (e.g., 10k entries): measure
  preview + apply time before/after, test tag-resolution caching, and verify
  final entry field/tag states match single-entry behavior.

Notes & Caveats

- `library.add_tags_to_entries` accepts lists but may internally commit per
  pair; for best results add a transactional bulk-write API or wrap multiple
  writes inside one session/transaction.
- If the library lacks bulk fetch/write APIs, implement batching inside the UI
  layer by grouping calls and wrapping them in a single DB transaction where
  library internals allow.
- Keep behavior-compatible: deduplication, ordering, and existing-value
  semantics must match current single-entry logic.

Files to change (suggested)

- src/tagstudio/qt/mixed/paths_to_fields.py (apply batching, pre-resolve tags,
  create value types, chunked progress)
- src/tagstudio/core/library/alchemy/library.py (optional) — add bulk API
  `add_fields_to_entries` or improve `add_tags_to_entries` to commit once per
  batch.

Quick test checklist

- functional_large_preview_apply: run preview+apply on 10k entries and compare
  times before/after.
- tag_resolution_cache_test: ensure get_tag_by_name called once per distinct
  name and tags added properly.
- batch_add_field_consistency: ensure batched writes produce identical fields
  and values ordering compared to single-entry behavior.

Next steps

- Implement batched fetch and tag pre-resolution in
  `src/tagstudio/qt/mixed/paths_to_fields.py` as a first patch, then run
  tests/benchmarks.
