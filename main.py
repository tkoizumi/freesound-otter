"""Download highly rated sounds from Freesound, five minutes at a time.

Otter runs this file with the integration directory as the working directory and
the `otter` package already on PYTHONPATH. Otter owns the schedule, the retries,
the timeout, the durable state and the logs; this file only describes one run:

  1. search one page of Freesound (each run looks at the next page, wrapping
     around at the end),
  2. keep the results that are new and rated well,
  3. download them into DOWNLOAD_DIR,
  4. remember what we took so the next run takes something else.

*What* is downloaded is entirely settings: SEARCH_QUERY for free text,
SEARCH_FILTER for a precise expression, and empty means "anything". See README.md.
"""

import os
import time
from pathlib import Path

from otter import run

from freesound_client import Freesound, FreesoundError, TooLarge
from rules import (
    exceeds_max,
    extension_for,
    keep,
    max_bytes_from_mb,
    preview_url,
    search_filter,
    sound_path,
)

#: Where downloads go unless DOWNLOAD_DIR says otherwise.
DEFAULT_DOWNLOAD_DIR = Path.home() / "Desktop" / "freesound_audio_files"

#: How many sound ids we remember. Larger than any realistic backlog, small
#: enough that the stored state stays tiny.
SEEN_LIMIT = 5000


def setting(name, default):
    """An environment setting, falling back when it is missing or empty."""
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def required(name):
    """An environment setting that has no sensible default."""
    value = os.environ.get(name)
    if not value:
        raise FreesoundError(
            "%s is not set. Add it to otter.env at the project root, then "
            "restart the runtime." % name
        )
    return value


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@run
def main(ctx):
    # ---- settings -------------------------------------------------------- #
    api_key = required("FREESOUND_API_KEY")
    download_dir = Path(setting("DOWNLOAD_DIR", str(DEFAULT_DOWNLOAD_DIR))).expanduser()
    download_dir.mkdir(parents=True, exist_ok=True)

    query = setting("SEARCH_QUERY", "")
    extra_filter = setting("SEARCH_FILTER", "")
    min_avg_rating = float(setting("MIN_AVG_RATING", "4.0"))
    min_num_ratings = int(setting("MIN_NUM_RATINGS", "3"))
    page_size = int(setting("PAGE_SIZE", "50"))
    max_downloads = int(setting("MAX_DOWNLOADS_PER_RUN", "5"))
    budget_seconds = int(setting("RUN_BUDGET_SECONDS", "240"))
    http_timeout = int(setting("HTTP_TIMEOUT_SECONDS", "60"))
    seen_limit = int(setting("SEEN_LIMIT", str(SEEN_LIMIT)))
    download_format = setting("DOWNLOAD_FORMAT", "preview").lower()
    max_download_mb = setting("MAX_DOWNLOAD_MB", "0")
    max_bytes = max_bytes_from_mb(max_download_mb)
    dry_run = setting("DRY_RUN", "0") not in ("0", "", "false", "False")

    seen_ids = set(ctx.state.get("seen_sound_ids") or [])
    page = int(ctx.state.get("next_page") or 1)

    # ---- credentials ----------------------------------------------------- #
    freesound = Freesound(
        api_key,
        api_base=setting("FREESOUND_API_BASE", "https://freesound.org/apiv2"),
        timeout=http_timeout,
    )
    access_token = None
    if download_format == "original":
        access_token = original_download_token(ctx, freesound, api_key)

    filter_text = search_filter(extra_filter, min_avg_rating, min_num_ratings)

    ctx.log.info(
        "searching Freesound",
        query=query,
        filter=filter_text,
        page=page,
        page_size=page_size,
        format=download_format,
        dry_run=dry_run,
    )

    # ---- search ---------------------------------------------------------- #
    response = freesound.search(
        query=query,
        filter_text=filter_text,
        sort=setting("SORT", "rating_desc"),
        page=page,
        page_size=page_size,
    )
    sounds = response.get("results") or []
    total_matches = int(response.get("count") or 0)

    # ---- download -------------------------------------------------------- #
    deadline = time.monotonic() + budget_seconds
    downloaded = skipped = failed = too_big = 0

    for sound in sounds:
        if downloaded >= max_downloads:
            break
        if time.monotonic() > deadline:
            ctx.log.warning("run budget reached; the rest waits for the next run")
            break
        if not keep(sound, seen_ids, min_avg_rating, min_num_ratings):
            skipped += 1
            continue

        destination = sound_path(download_dir, sound, extension_for(sound, download_format))
        if destination.exists():
            seen_ids.add(sound["id"])  # already on disk; never look at it again
            skipped += 1
            continue

        # `filesize` describes the *original* upload, so this check is exact only
        # when we are taking originals. A preview's size follows its duration,
        # and is checked against the response itself once the download starts.
        if download_format == "original" and exceeds_max(sound.get("filesize"), max_bytes):
            too_big += 1
            ctx.log.info(
                "skipped: bigger than MAX_DOWNLOAD_MB",
                id=sound.get("id"),
                bytes=sound.get("filesize"),
                max_download_mb=max_download_mb,
            )
            continue

        if dry_run:
            ctx.log.info(
                "dry run: would download",
                sound=sound.get("name"),
                id=sound.get("id"),
                rating=sound.get("avg_rating"),
            )
            downloaded += 1
            continue

        try:
            url, token = download_target(sound, download_format, access_token)
            freesound.download(
                url, str(destination), access_token=token, max_bytes=max_bytes
            )
        except TooLarge as error:
            # Not a failure: the file is simply bigger than we are willing to
            # take. Leaving it out of `seen_ids` means raising the limit later
            # will pick it up.
            too_big += 1
            ctx.log.info(
                "skipped: bigger than MAX_DOWNLOAD_MB",
                id=sound.get("id"),
                bytes=error.size_bytes,
                max_download_mb=max_download_mb,
            )
            continue
        except FreesoundError as error:
            # One bad sound must not stop the others, and we leave it out of
            # `seen_ids` so the next run tries it again.
            failed += 1
            ctx.log.warning("download failed", id=sound.get("id"), error=str(error))
            continue

        seen_ids.add(sound["id"])
        downloaded += 1
        ctx.log.info(
            "downloaded",
            sound=sound.get("name"),
            file=destination.name,
            rating=sound.get("avg_rating"),
            license=sound.get("license"),
        )

    # ---- remember where to look next time -------------------------------- #
    # We walk the rating-sorted pages and wrap around at the end. Newly rated
    # sounds drift in at the top of page 1, so nothing is missed for long.
    total_pages = max(1, -(-total_matches // page_size))  # ceiling division
    next_page = page + 1 if page < total_pages else 1

    summary = {
        "page_searched": page,
        "next_page": next_page,
        "results_on_page": len(sounds),
        "downloaded": downloaded,
        "skipped": skipped,
        "too_big": too_big,
        "failed": failed,
        "max_download_mb": max_download_mb,
        "total_matches": total_matches,
        "directory": str(download_dir),
        "dry_run": dry_run,
        "finished_at": now_iso(),
    }

    if not dry_run:
        ctx.state.set("seen_sound_ids", sorted(seen_ids)[-seen_limit:])
        ctx.state.set("next_page", next_page)
    ctx.state.set("last_run", summary)
    ctx.log.info("run finished", **summary)


def download_target(sound, download_format, access_token):
    """Return ``(url, access_token)`` for one sound: its original, or a preview."""
    if download_format == "original":
        url = sound.get("download")
        if not url:
            raise FreesoundError("no download URL for sound %s" % sound.get("id"))
        return url, access_token
    url = preview_url(sound)
    if not url:
        raise FreesoundError("no preview URL for sound %s" % sound.get("id"))
    return url, None


def original_download_token(ctx, freesound, api_key):
    """An OAuth2 access token, for original-quality downloads only.

    Freesound's credential page has two columns, "Client id" and "Client secret /
    Api key": the second is the very same string we already use as the API key,
    so it doubles as the OAuth2 client secret. Only the client id is genuinely
    new here. FREESOUND_CLIENT_SECRET overrides it if your values ever differ.

    Freesound rotates the refresh token every time it is used, so the newest one
    is kept in Otter's state; the environment is only the starting point.
    """
    client_id = required("FREESOUND_CLIENT_ID")
    client_secret = os.environ.get("FREESOUND_CLIENT_SECRET") or api_key
    refresh_token = ctx.state.get("freesound_refresh_token") or os.environ.get(
        "FREESOUND_REFRESH_TOKEN"
    )
    if not refresh_token:
        raise FreesoundError(
            "DOWNLOAD_FORMAT=original needs FREESOUND_REFRESH_TOKEN. Get one "
            "once with the OAuth2 flow, put it in otter.env, or set "
            "DOWNLOAD_FORMAT=preview to take MP3 previews instead."
        )
    access_token, new_refresh = freesound.refresh_access_token(
        client_id, client_secret, refresh_token
    )
    if new_refresh:
        ctx.state.set("freesound_refresh_token", new_refresh)
    return access_token
