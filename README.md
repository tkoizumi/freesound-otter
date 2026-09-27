# freesound

Download highly rated sounds from [Freesound](https://freesound.org) into a
folder on this Mac, five minutes at a time. What it looks for is up to you: a
search query and a filter, both plain settings.

Each run searches one page of Freesound, keeps the results that are new and
rated well, downloads them, and remembers what it took so the next run takes
something else. Otter provides the schedule, the retries, the timeout, the
durable state and the logs.

```text
Freesound API (search + previews)        Otter                    Your Mac
  /apiv2/search/  ──────────────────►  freesound/main.py  ──►  ~/Desktop/freesound_audio_files/
    your query + filter, sorted by         state: seen ids,        123_Drum_loop.wav
    rating; avg_rating >= 4,                     page cursor,
    num_ratings >= 3                             last run
```

Files in this directory:

| File | What it is |
| --- | --- |
| `otter.yaml` | when it runs (every 5 minutes) and every setting |
| `main.py` | what one run does: search, choose, download, remember |
| `freesound_client.py` | all the network code, in one place |
| `rules.py` | the decisions only: what counts as a keeper, filenames |
| `tests/test_rules.py` | offline tests for `rules.py` |

## Setup

### 1. Get a Freesound API key

Sign in at Freesound, request a credential at
<https://freesound.org/apiv2/apply>, and copy the **Client secret / API key**
column (the long string). That one value is your API key. The **Client id**
beside it is not needed for search or MP3 previews — only for original-quality
downloads, below.

### 2. Put it in the project's secrets file

Secrets live in one file, at the repository root (`otter_examples/otter.env`),
because the daemon has one environment for every integration. `otter.yaml` only
*names* the key; the value never goes in there.

```bash
cd /Users/taka/Desktop/otter_examples
$EDITOR otter.env
```

Add:

```sh
FREESOUND_API_KEY=your-api-key-here
```

### 3. Start the runtime

The daemon reads `otter.env` **when it starts**, not when a run starts, so after
editing the file it has to be restarted:

```bash
otter validate freesound
otter release freesound          # runs execute an immutable snapshot of this directory
otter stop && otter start --detach
otter run freesound              # a manual run, right now
```

You should see log lines like `downloaded` ending with `run finished`.

### 4. Done

The cron trigger fires every five minutes. Check on it any time:

```bash
otter integrations --schedule              # next run time and last outcome
otter runs --integration freesound --limit 10
otter logs <run-id> --follow
otter state get freesound last_run
```

## Choosing what to download

Two settings decide what Freesound is asked for, both under `env:` in
`otter.yaml`. With both empty, the only filter is the rating thresholds — you
get the highest-rated sounds of any kind.

| You want | Set |
| --- | --- |
| a particular word | `SEARCH_QUERY: kick` |
| a phrase | `SEARCH_QUERY: "drum loop"` |
| to exclude a word | `SEARCH_QUERY: rain -thunder` |
| a tag | `SEARCH_FILTER: 'tag:drum'` |
| a Broad Sound Taxonomy category | `SEARCH_FILTER: 'category:"Sound effects" AND subcategory:"Animals"'` |
| percussion as a category | `SEARCH_FILTER: '(category:"Music" AND subcategory:"Solo percussion") OR (category:"Instrument samples" AND subcategory:"Percussion")'` |
| uncompressed mono shorts | `SEARCH_FILTER: 'type:wav AND channels:1 AND duration:[0 TO 5]'` |

Single-quote the value in YAML when it contains a colon, as the `SEARCH_FILTER`
rows do — otherwise YAML may read it as a nested mapping rather than a string.

`SEARCH_QUERY` is free text; Freesound matches it against tags, names,
descriptions and pack names, and accepts `+term` (required), `-term` (excluded)
and `"quoted phrases"`. `SEARCH_FILTER` is Freesound's own filter syntax, and the
[Broad Sound Taxonomy](https://freesound.org/help/broad-sound-taxonomy/) is the
most reliable way to ask for a *kind* of sound, because it is assigned per sound
rather than guessed from tags.

`MIN_AVG_RATING` and `MIN_NUM_RATINGS` are appended to whatever you put in
`SEARCH_FILTER`, so you never write the rating part yourself.

## Size limits and the run budget

`MAX_DOWNLOAD_MB` skips anything larger than the given number of megabytes; `0`
means no limit. A file that is too big is **skipped, not failed**, and stays out
of the state list — so raising the limit later will pick it up on a later sweep.

The check is against the download's own `Content-Length`, so an oversized file is
refused before a single byte is written. If a server does not declare a size, the
download is abandoned the moment it passes the limit and the partial file is
deleted; either way you are not left with a `.part` file to re-fetch.

For previews this is a backstop, not a precise control: a preview is a
fixed-bitrate re-encode, so its size follows the sound's **duration**
(~0.94 MB per minute at hq, ~0.47 at lq), not the original file's `filesize`.
Use a `duration:` filter in `SEARCH_FILTER` when you want to bound size exactly.

Keep `MAX_DOWNLOADS_PER_RUN × MAX_DOWNLOAD_MB` comfortably inside what your link
can move in `RUN_BUDGET_SECONDS`. `cdn.freesound.org` can be slow — measured at
~80 KB/s from one Mac, against 12 MB/s to a reference host — and the defaults
(`2 × 5 MB`) are sized to finish in about two minutes at that speed.

## Where the files go

By default: `~/Desktop/freesound_audio_files` (created on the first run).

Each sound is written as `<freesound-id>_<name>.<ext>` — nothing else. The id
prefix is the Freesound sound id, so the source page is always
`https://freesound.org/s/<id>/`, where the author and licence can be looked up if
you need to credit it later.

To choose a different folder, edit `DOWNLOAD_DIR` under `env:` in `otter.yaml`
(an absolute path, or `~/...`), then:

```bash
otter release freesound
```

Because the value comes from `otter.yaml`, setting `DOWNLOAD_DIR` in `otter.env`
will **not** override it — a manifest value always wins. If you would rather keep
the folder per-machine, delete the `DOWNLOAD_DIR` line from `otter.yaml` (the
code falls back to `~/Desktop/freesound_audio_files`) and set it in `otter.env`
instead.

## Settings

Everything below lives under `env:` in `otter.yaml`. Edit it, then run
`otter release freesound` again — a running release does not change on its own.

`otter reload` is **not** a substitute. It re-reads the integrations directory and
will make `otter inspect` show your new value, but a run still executes the active
release, so it keeps using the old one. Release.

| Setting | Default | Meaning |
| --- | --- | --- |
| `SEARCH_QUERY` | empty | Free text sent to Freesound's search: tags, names, descriptions, pack names. Empty = any sound. |
| `SEARCH_FILTER` | empty | A Freesound filter expression (`tag:drum`, `category:"…"`, …). Empty = no extra restriction; the rating thresholds are appended to it. |
| `MIN_AVG_RATING` | `4.0` | "Highly rated": average star rating, 0–5. |
| `MIN_NUM_RATINGS` | `3` | …by at least this many people, so one enthusiastic vote is not enough. |
| `SORT` | `rating_desc` | Highest rated first. |
| `PAGE_SIZE` | `50` | Sounds per search, 150 max. |
| `MAX_DOWNLOADS_PER_RUN` | `2` | Stops a run from hoarding; the rest wait for the next tick. |
| `RUN_BUDGET_SECONDS` | `240` | Stop early and continue next run, instead of being killed by the 300 s timeout. |
| `MAX_DOWNLOAD_MB` | `5` | Skip any file bigger than this. `0` means no limit — download everything. |
| `DOWNLOAD_FORMAT` | `preview` | `preview` = MP3, no OAuth2. `original` = the file as uploaded (see below). |
| `DOWNLOAD_DIR` | `~/Desktop/freesound_audio_files` | Where audio lands. Absolute, or `~/...`; created if missing. |
| `HTTP_TIMEOUT_SECONDS` | `60` | Give up on one HTTP request after this long. Raise it on a slow link. |
| `SEEN_LIMIT` | `5000` | How many sound ids to remember between runs. A speed knob; files on disk are checked too. |
| `DRY_RUN` | `0` | `1` logs what *would* happen and writes nothing. Good for a first run. |
| `FREESOUND_API_BASE` | `https://freesound.org/apiv2` | Only useful for pointing at a mock while developing. |

Every one of these lives in `freesound/otter.yaml`, so changing behaviour never
means editing Python. `trigger:`, `timeout`, `concurrency` and `retry` sit in the
same file.

## Pausing and resuming

Pausing is a manifest change, not a CLI command. Comment out the `trigger:` block
in `otter.yaml`:

```yaml
# trigger:
#   cron: "*/5 * * * *"
```

then release:

```bash
otter release freesound
```

The cron stops as soon as the release activates — no daemon restart — and
`otter run freesound` still works whenever you want a manual run. Uncomment the
block and release again to resume. `otter integrations --schedule` shows only the
integrations with a live cron, so a paused one drops off that list.

To stop *every* integration at once, use `otter stop` instead. To see what a
paused integration would do without it doing anything, set `DRY_RUN: "1"` and run
it by hand.

## Getting original quality (optional)

By default this downloads Freesound's ~128 kbps MP3 **preview**, which needs no
extra credentials. The **original** file (WAV, FLAC, …) is served only to an
application acting on a user's behalf, so it needs OAuth2 as well as the API key.

1. In your credential at <https://freesound.org/apiv2/apply>, make sure it has a
   **redirect URL**. If your application cannot receive a request, use the
   Freesound page it offers you instead — it prints the code on screen.
2. Visit this URL in a browser, logged in as yourself, and click Authorize:

   ```text
   https://freesound.org/apiv2/oauth2/authorize/?client_id=YOUR_CLIENT_ID&response_type=code
   ```

3. Copy the `code` from the redirect, then exchange it within 10 minutes:

   ```bash
   curl -X POST https://freesound.org/apiv2/oauth2/access_token/ \
     -d "client_id=YOUR_CLIENT_ID" \
     -d "client_secret=YOUR_CLIENT_SECRET" \
     -d "grant_type=authorization_code" \
     -d "code=THE_CODE"
   ```

4. Add the client id and the refresh token to `otter.env`, and switch the format
   in `otter.yaml`:

   ```sh
   FREESOUND_CLIENT_ID=...
   FREESOUND_REFRESH_TOKEN=...
   ```

   ```yaml
   DOWNLOAD_FORMAT: original
   ```

   The client id is the only genuinely new value here: the **Client secret / API
   key** you already set as `FREESOUND_API_KEY` doubles as the OAuth2 client
   secret. Set `FREESOUND_CLIENT_SECRET` as well only if your two values ever
   differ.

5. `otter release freesound && otter stop && otter start --detach`, then
   `otter run freesound`.

Freesound issues a **new refresh token every time one is used**, so the
integration keeps the newest in Otter's state and the value in `otter.env` is
only the starting point. If you ever delete that state, redo step 3.

Freesound's API is free for **non-commercial** use, and its rate limits are
60 requests/minute (2000/day) for search and 30 requests/minute (500/day) for
original-file downloads. The defaults here stay well inside both: one search per
five minutes, and at most five downloads per run.

## Tests

```bash
python3 -m unittest discover -s freesound/tests
```

They cover `rules.py` only — the network code needs real credentials, and the
rest is Otter's job.
