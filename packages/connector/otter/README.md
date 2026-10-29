# cognee-community-connector-otter

Pull Otter.ai conversations into cognee as readable meeting documents. Each document
contains the original speaker-labelled, timestamped transcript, summary, action items,
insights, outline, and selected provenance. This is a Path B dlt document source.

## Requirements and installation

- An Otter Enterprise workspace with Public API access.
- Python 3.11–3.13 and cognee 1.6.3 (pinned for scoped document cleanup, including a
  successful empty replacement). No dependency on the unmerged Miro connector.
- An LLM/embedding provider configured for cognify/search. Offline tests mock those calls.

From this repository:

```bash
cd packages/connector/otter
uv sync --group dev
```

## Credentials

Create an API key in Otter **Integrations → Developer → Create key**. Configure it
through dlt, either in the calling project's `.dlt/secrets.toml`:

```toml
[sources.otter]
api_key = "YOUR_OTTER_API_KEY"
```

or through the environment:

```bash
export SOURCES__OTTER__API_KEY="YOUR_OTTER_API_KEY"
```

You can also pass `api_key=` from your own secret manager. The connector does not
load a repository-root `.env` or store credentials in documents. Keep secrets files
out of Git. dlt resolves `.dlt/secrets.toml` relative to the working project; run the
example from the package directory when configuring credentials there.

## Usage and scope

```python
import cognee
from cognee_community_connector_otter import otter_source

await cognee.remember(
    otter_source(),
    dataset_name="otter-meetings",
    primary_key="id",
    write_disposition="replace",
    max_rows_per_table=0,
    self_improvement=False,
)
```

Selection options:

- `otter_source()` selects owned conversations.
- `otter_source(include_shared=True)` includes conversations shared with the user.
- `otter_source(channel_ids=["channel-id"])` selects the union of those channels.
  Otter automatically includes shared conversations for channel filters.
- `otter_source(conversation_ids=["conversation-id"])` fetches only those IDs directly.

Channel IDs and conversation IDs are mutually exclusive. Duplicates are collapsed;
an empty explicit selection is rejected to avoid accidentally broadening the scope.
Use a dedicated cognee dataset for each independent account/selection. Changing a
selection within the same dataset makes removed selections eligible for cleanup.

Run `uv run python examples/example.py` for ingest, cognify, and search. Use
`--conversation-id` (repeatable), `--channel-id` (repeatable), or `--include-shared`.

## Synchronization and deletion

The API lists conversations newest first with pagination cursors. It documents no
date/modified filter or reliable conversation modification timestamp. A pagination
cursor is used only within a run; it is not saved as a change watermark.

Every run reads the complete selected inventory and fetches its conversation details.
Rendering excludes volatile retrieval timestamps and sorts unordered metadata. cognee
uses stable row-content hashes to avoid reprocessing unchanged documents. Old transcript
edits and speaker corrections are detected even when `created_at` stays unchanged.
This is incremental processing in cognee, **not incremental API extraction**.

All pages and detail responses are validated and buffered before any rows are yielded.
Only a complete successful snapshot replaces staging. Rows absent from that snapshot
are reconciled by cognee's existing document cleanup, including an empty final snapshot.
The connector does not directly modify cognee databases or generate fake tombstones.

- A documented 404 on an explicitly requested conversation is treated as not found.
- A listed conversation returning 404 during detail retrieval aborts that run; the next
  successful inventory can reconcile its disappearance.
- Authentication errors, missing channels, exhausted retries, malformed payloads,
  repeated cursors, missing relationships, and unavailable transcripts abort the run.
- Null transcripts or unfinished generated content abort instead of replacing existing
  content with an incomplete version. Retry after processing or temporarily select only
  ready conversations. Optional null action items, insights, and outlines are accepted.
- Retries are bounded, honor `Retry-After`, and pace requests below 10/second per client.
  A requested retry delay above 120 seconds aborts for a later rerun.

Otter supplies no transactional snapshot token. Complete pagination does not guarantee
that the upstream account stayed unchanged throughout the run. Visibility loss can look
like removal in an otherwise successful inventory; cleanup reflects the configured visible
scope, rather than claiming to distinguish deletion from unsharing. Separate concurrent
connectors using one key must share its rate budget outside this package.

## Content and privacy

One document uses the original conversation ID as its primary key. Title and Otter URL
are passed through cognee's document metadata. Owner, participant names, creation date,
and channel names/IDs are rendered in the document so they remain available to cognify.
Transcript text is preserved, including unidentified speaker labels; the connector does
not infer identities or invent word-level timing.

Only the documented text transcript format is supported. Audio is not downloaded.
Email fields, sharing email lists, and conference join links are omitted. Transcript
text and summaries may themselves contain sensitive information; this is not a PII
redaction system. Data follows the user's cognee storage and LLM provider configuration.

## Verification

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv build
```

Fixtures are synthetic examples of the documented schema, not captured API responses.
Tests cover REST requests, pagination, selections, deterministic rendering, speaker edits,
retry behavior, complete and empty replacements, and failures preserving stored data.
The graph integration test uses actual cognee storage with mocked LLM/embedding calls;
it checks unchanged-content reuse, failed-sync preservation, and deletion through the
last document. Tests block outgoing socket connections.

Live validation requires an Enterprise API key and is still outstanding. Before release,
verify selection, real transcript/relationship payloads, an old transcript edit, a deletion,
and a final-empty sync against a dedicated test dataset. Never claim live validation from
the mocked tests. Prepare the Mergetober blog with an actual meeting-recall use case after
that run.

## Design references

- [Assigned issue and proposed approach](https://github.com/topoteretes/cognee/issues/4741)
- [Otter Public API](https://help.otter.ai/hc/en-us/articles/36130822688279-Otter-ai-Public-API)
- [Building a cognee Integration — Path B](https://app.notion.com/p/topoteretes/Building-a-cognee-Integration-3c937007fa8281b18e83fcc24f86004a)
- `packages/connector/notion/` for document-source conventions.
- `cognee/tasks/ingestion/dlt_utils.py` and `resolve_dlt_sources.py` for routing,
  content-derived identity, dataset isolation, and cleanup.

The guide's plural `packages/connectors/` wording differs from the repository: this
package uses the actual `packages/connector/` directory specified in the issue.
