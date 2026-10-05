# Sequencing data library

The authenticated `#data` workspace starts with a paginated table of all independent files, including merged outputs and individual RNA-seq libraries.
Recorded input files are nested beneath their output in a list that is collapsed by default. Intermediate merges can be expanded in turn.
Input files retain their relationship status, path copying, and file details. Only inputs explicitly linked to a specific output are nested. Tissue, condition, and replicate files are not grouped merely because they share a sample or data type.
Curated rows marked `display_group=retained_original` are kept in a separate, initially collapsed **Retained originals** list by sample and data type. This list does not imply a merge relationship. It loads 25 files at a time when opened, with path copying and metadata details. Unmarked independent libraries remain in the main table. No physical sequencing file is removed.
The left library narrows that same table by **project → species → sample → data type**.
Its own search finds project/species names and sample names or IDs (including cultivars and individuals). Search ignores case and surrounding whitespace, retains matching ancestors, and opens matching branches. Entries still toggle open/closed. Clearing the query restores the ordinary tree expansion state; selecting an entry filters the file table. Typing alone does not change the table selection. The same search is available in the mobile menu.
Rows show sample/species, project, data type, read count, total bases, and Q30 percentage; clicking a filename opens its details. Nested input files and retained originals also show Q30 when available.
Each file-table column has a sorting button; sample and species can be sorted separately in their shared column. Text columns start ascending and numeric columns start descending; clicking again reverses the direction. Sorting applies to the complete filtered result before pagination, resets to page 1, and preserves merged-input groups. Unknown statistics stay last in either direction, while measured zero remains sortable. Search and role filters retain the selected order; choosing a different hierarchy scope resets it to the existing representative/merged-first default.
It supports file and metadata search, role filters, pagination, path copying, and recorded merge inputs in Korean and English.
The web table is read-only. The Cowork MCP can register metadata and update paths after a move. Neither interface moves, deletes, downloads, merges, or runs statistics on sequencing files.

## Data availability

The separate **Data availability** entry in the left DATA LIBRARY opens `#data/availability`.
Each project has a sample-by-data-type matrix. The availability tab initially shows a project selector and a prompt, without any matrix. Selecting a project reveals its matrix, sample search, and legend; clearing the selection hides them again. Switching projects clears narrower species/sample scope. The project/species/sample tree can also select a project or narrow its scope.
RNA-seq columns distinguish tissues and conditions, including floating/sinking Wolffia individuals.
Green-rice RNA metadata separates leaf, root, and flower libraries from pooled stem/root/flower libraries. The `pooling_ST-RT-FL` label means a mixture of stem, root, and flower (user confirmed); it is one pooled category, not three independent tissue datasets. Filename labels 1–3 in separate tissue libraries remain inferred replicate labels, while paired-read suffixes never establish replication.
Selecting a present or review cell opens that sample's data-type file list. Browser back returns to the matrix.
The default `#data` workspace remains the file table. Both views share catalog permissions and support Korean, English, search, and mobile navigation.

The exporter adds a small `availability` summary to each dataset in the private snapshot. This travels with the existing hierarchy response, so the matrix does not fetch all file pages.
Presence means an eligible file is registered, not that its contents or inferred assignment have been newly validated.
Unclassified data, unlinked declared datasets, and pending Green-rice scope show **Needs review**. No matching registration shows **Not registered**, which does not assert biological absence.
Fail reads do not establish presence. For RNA-seq, `×N` counts distinct nonempty replicate labels per tissue and condition; paired ends, lanes and merged copies with the same label count once. Mixed labeled/unlabeled groups show presence without a count.
Only explicitly user-confirmed replicate metadata omits the asterisk; inferred labels keep the biological-replication caveat. No new tissue, condition, or replicate identity is guessed from a filename by this exporter.

## Private snapshot

Build a snapshot from the curated report directory, outside the public web output:

```sh
python3 scripts/build_catalog.py --source /absolute/path/to/curated-reports --stats /absolute/path/to/file-stats.csv --output /private/path/catalog.json
```

The source contains `samples.csv`, `sample-data-types.csv`, `reports.csv`, and the `file-organization/` CSV exports.
The exporter reads metadata only and atomically replaces the snapshot with owner-only permissions.
Rows explicitly marked `catalog_exclusion=run_id_chunk` are omitted from the web catalog and its nested input lists. Their private inventory rows and merge relationships remain in the source CSVs; outputs retain their merged role and their own statistics. Dataset and unresolved counts exclude those rows. This is a catalog policy, not a filesystem deletion or a filename-based rule applied to other independent libraries.
The optional `--stats` argument imports a saved SeqKit CSV by exact path and matching stored file size. `num_seqs` becomes `stats.reads`, and `sum_len` becomes `stats.bases`; these are per-file counts, so paired-end R1 and R2 are separate.
The optional `Q30(%)` column becomes `stats.q30_percent`, the percentage of bases with a Phred quality score of at least 30 ([SeqKit stats](https://bioinf.shenwei.me/seqkit/usage/#stats)). Only completed, nonempty files with a finite value between 0 and 100 supply Q30. Missing, invalid, empty-file, failed, and size-mismatched results remain null and display `—`; a measured 0% remains 0%. The current saved scan used Sanger/Phred+33 encoding. No quality percentages are inferred from a provider's sample-level report or averaged across component files.
Only completed results supply numeric counts. Genuine zero values stay zero; pending, failed, invalid, or mismatched results stay distinct from zero. A pending merged output never inherits a sum of its inputs.
Statistics are a snapshot of the CSV at export time, with `computed_at` retained per file. Before the first MCP write, rerun the export with the updated CSV and replace the private snapshot to import later results. After the catalog has been saved to the database, CSV exports are bootstrap inputs only; replacing the JSON cannot overwrite database edits. Updating statistics on existing database records is outside the registration/relocation API. No FASTQ is opened by this exporter.
Keep the snapshot out of Firebase Hosting, frontend assets, and source control.

Statistics collection scope: do not schedule additional read/base/Q30 calculations for uncompressed ONT `.fastq` files (user decision, 2026-10-05). Keep their catalog entries and any previously completed statistics. Missing values may remain blank. Compressed ONT `.fastq.gz`/`.fq.gz` files remain eligible. Apply this exclusion when preparing future calculation inputs; this is a collection policy, not a catalog filter or a reason to discard existing results.

Add private settings to the hub's `HUB_WEB_CONFIG` JSON:

```json
{
  "catalog_path": "/data/catalog.json",
  "catalog_access": "approved",
  "catalog_write_access": "approved"
}
```

These fields supplement the existing Firebase settings; they are excluded from `/web/config.json`.
The deployed lab policy is `approved`: all authenticated, linked, enabled hub users can view the library and data availability after account approval. Pending and disabled accounts cannot access the catalog.
The lab's write policy is also `approved`: enabled users can register files and update moved paths through their personal MCP token, with an audit history.
If omitted, the default `admin` policy permits configured web administrators only.
No catalog setting changes server, SSH, or job permissions.
Restart the hub when changing configuration. Before the first MCP write, replacing the snapshot at the configured path does not require a restart.

## MCP registration and moved paths

The [cowork-data-library skill](../skills/cowork-data-library/SKILL.md) is included in the client installer and its update flow. Reconnect the MCP after updating to load the six `catalog_*` tools (16 tools in total).

The skill automatically applies when an agent is asked to move or rename FASTQ files or their containing directories, including over SSH. The move request also authorizes synchronizing the registered paths; the user need not name the skill or separately request a database update. The agent records existing file IDs and old/new paths before moving, verifies successful destinations, then previews and commits their catalog relocation. Directory moves retain relative paths and cover registered descendants. Failed or incomplete transfers keep their old catalog paths, and copies that retain the source are not treated as relocations. If saving fails after a physical move, the agent retains a recovery record and reports the unsynchronized files. This is an agent workflow, not a filesystem watcher for shell commands issued outside the agent.

All MCP catalog calls use the existing personal hub token. `catalog_access` still controls reads; `catalog_write_access` defaults to `admin`, permitting personal tokens belonging to configured `admin_users`. The optional `approved` policy permits enabled hub users with catalog read access to edit the shared catalog. A bootstrap administrator token or worker token cannot use these user routes. These settings do not grant SSH or filesystem permissions.

- `catalog_overview`, `catalog_files`, `catalog_file`, and `catalog_history` inspect the hierarchy, file metadata, revision, and change history.
- `catalog_register_files` registers project, species, sample, data type, storage node, absolute FASTQ path, and observed byte size. Tissue, condition, replicate, R1/R2, report/provider/receipt dates, existing statistics, notes, and confirmed merge inputs are optional. New hierarchy creation requires `create_missing=true`; names are resolved case-insensitively within their parent scope.
- `catalog_relocate_files` updates an existing file ID after a verified unchanged-file move or rename. It requires the old path, new path, storage node, observed byte size, and `content_unchanged=true`. Its ID, sample, statistics, and input relationships survive the move. Referencing merged files receive the updated input path in the same transaction.

Both writes default to `dry_run=true`. Inspect the preview, then submit the same `request_key`, `expected_revision`, and content with `dry_run=false`. A request contains 1–100 files and is atomic. The revision prevents overwriting concurrent changes; the request key makes uncertain network retries safe. A repeated completed request returns the prior result, while reusing a key with different content is rejected. Missing statistics remain unknown, not zero. R1/R2 with the same replicate label count once, and pooled tissues remain one category.

Paths are normalized absolute `.fastq`, `.fq`, `.fastq.gz`, or `.fq.gz` paths. This catalog treats an absolute path as globally unique across storage nodes; it does not yet represent two different files with the same path on different servers. The hub validates registered/enabled nodes and metadata, not remote file existence or file contents. The agent must verify the destination with the user's allowed SSH access; equal byte sizes alone are not proof that an unrelated file is identical. Relocation is unsuitable for recompression or content replacement.

Schema 8 stores one transactional catalog document in `catalog_state`, idempotency results in `catalog_requests`, and per-file before/after records and actors in `catalog_events`. Before the first successful write, reads use the configured private JSON. The first write imports the entire snapshot atomically; all web and MCP reads subsequently use the database. No manual CSV rebuild is needed after MCP registration or relocation. Back up these tables with the hub database. Once writes have occurred, do not roll back to a JSON-only hub without first exporting and reconciling the database catalog.

Bootstrap snapshots must include per-file condition and replicate evidence from the current exporter. Adding to an older RNA dataset with incomplete per-file metadata is rejected, so it cannot erase an existing condition summary.

HTTP equivalents are `GET /v1/catalog`, `/v1/catalog/files`, `/v1/catalog/files/{id}`, `/v1/catalog/files/{id}/history`, and `POST /v1/catalog/register` or `/v1/catalog/relocate`. They use personal-token authentication. The existing `/v1/web/catalog` routes retain Firebase authentication. Both read the same repository and return no-store responses.

## API and evidence

- `GET /v1/web/catalog` returns the hierarchy without individual file paths.
- `GET /v1/web/catalog/files` returns all assigned files or narrows them by `project`, `species`, `sample`, and `dataset`. It also accepts `q`, `role`, `grouped`, `page`, and `page_size` (maximum 100). Invalid or incompatible selections return 404.
- `view=library` keeps explicitly retained originals out of the main table and returns filtered `original_groups` counts. `view=originals` provides those rows with normal search, sample/data type boundaries, authorization, and pagination. The default `view=all` preserves the complete existing API. These display flags do not change `inputs` or statistics.
- `grouped=true` resolves recorded input paths only within the same sample and data type. It removes nested files from the top level before filtering and pagination, returns nested metadata in `inputs[].file`, and lets searches on an input find its final output. Missing input metadata remains a path-only reference. The default API response remains flat for compatibility; the web table requests `grouped=true&role=all` initially. Merged-only filtering is an optional choice.
- `GET /v1/web/catalog/samples/{id}/files` accepts `dataset`, `q`, `role`, `page`, and `page_size` (maximum 100).
- Both file-list endpoints accept `sort_by=default|file|sample|species|project|data_type|reads|bases|q30` and `sort_order=asc|desc`. Text comparisons ignore case; equal values use path and file ID as stable tie-breaks. Omitting sorting preserves the original order.
- All routes require Firebase authentication and catalog access. Responses are not cached by the browser.
- Unknown metadata, provisional data types, inferred sample assignments, and proposed merge relationships remain distinct from confirmed metadata.
- Original and merged files can overlap. File counts and stored sizes are not unique sequence yield.
- A provider's report date is separate from the actual receipt date.
- Snapshot paths are inventory observations, not a fresh guarantee that every file still exists.

Validate with `tests/test_catalog.py`, `tests/test_catalog_write.py`, `tests/test_mcp.py`, and the catalog scenarios in `tests/web_catalog.cjs` (run by `tests/web_react.cjs`).
