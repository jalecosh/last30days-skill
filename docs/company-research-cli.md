# Company research CLI

`tools/research_company.py` is the ticker-first InvestmentBrain Reddit runner.
The ticker selects `config/companies/<TICKER>.json`; company names and aliases
are search metadata, never filenames or primary keys.

```powershell
python tools/research_company.py ADBE --plan
python tools/research_company.py --ticker ADSK
python tools/research_company.py ADBE --days 30 --as-of 2026-07-26
```

`--plan` performs no research. It prints the fully resolved configuration,
date range, query expansion, Reddit policy, and Markdown/metadata destinations.

## Configuration precedence

The resolved configuration is merged in this order:

1. `config/research-defaults.json`
2. `config/companies/<TICKER>.json`
3. CLI overrides (`--days`, `--sources`, `--intent`, `--freshness-mode`,
   `--cluster-mode`, `--output-directory`, and `--reddit-backend`)

Global Reddit policy is versioned and applies to every ticker:

```json
config/reddit/blocklist.json
config/reddit/preferences.json
```

The runner deliberately never passes `--subreddits` or
`--dedicated-subreddits`. In Last30Days, those flags activate targeted or
floor-exempt subreddit lanes. Omitting both retains global Reddit discovery so
unknown relevant communities remain eligible. The runner passes policy paths to
the engine, which filters blocked items only during normalization and applies
preferences only after the existing Reddit four-signal score. The resolved
policy versions and paths are recorded in the plan and result metadata.

Manage the one global blocklist with:

```powershell
python tools/reddit_policy.py add-block ExampleForum
python tools/reddit_policy.py remove-block ExampleForum
python tools/reddit_policy.py list-blocks
```

Names are case-insensitive, accept an optional `r/` prefix, and are stored in
lowercase canonical form. Duplicate normalized names are rejected.

Each execution writes adjacent ticker-led artifacts:

```text
output/results/ADBE-reddit-2026-06-27-to-2026-07-26.md
output/results/ADBE-reddit-2026-06-27-to-2026-07-26.json
```

The JSON includes configuration provenance, executed groups and queries,
backend choice, execution estimate, structured statistics, status, paths, and timestamp. The
transient engine plan is removed in a `finally` block.

## Structured statistics

The runner receives these from the engine's `--run-summary-output` JSON rather
than parsing Markdown:

- `raw_source_record_count`: source records returned by completed retrieval
  streams before normalization.
- `normalized_stream_item_count`: per-stream items after normalization, hard
  filters, eligibility gates, and per-stream limits, before cross-stream fusion.
- `deduplicated_candidate_count`: candidates returned by weighted fusion.
- `ranked_candidate_count_before_final_reddit_cap`: candidates after reranking,
  before the final Reddit post cap.
- `final_selected_count`: `Report.ranked_candidates` after the final Reddit cap;
  this is the structured list passed to clustering and rendering.
- `reddit.raw_posts`: raw Reddit records returned across subqueries.
- `reddit.posts_removed_by_blocklist`, `reddit.posts_removed_as_commentless`, and
  `reddit.posts_removed_as_zero_engagement`: mutually tracked Reddit retrieval
  filter counters (a record can be represented in more than one diagnostic
  counter where the underlying checks overlap).
- `reddit.posts_with_usable_comments`: raw Reddit records with comment evidence.
- `reddit.final_threads`: final Reddit source items after the final cap.

If the engine fails before writing a valid summary, metadata contains
`statistics: null` and `run_summary_error`; it never falls back to Markdown.
# Comment-enrichment limits

Company plans perform the existing query-scoped Reddit discovery first, then
deduplicate posts across the company before comment enrichment.  At most 24
unique posts are selected.  With a ScrapeCreators key, each selected post may
make one primary comment request and one public fallback request, for at most
48 network attempts.  Without a key, there are at most 24 public attempts.
An empty response is a successful empty outcome, not a backend failure; both
an empty and a failed primary response permit the public fallback.
