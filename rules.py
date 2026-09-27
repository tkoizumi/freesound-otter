"""The decisions, with no network access and no environment reads.

Keeping them here means they can be tested with plain `python3 -m unittest`,
with no daemon and no credentials -- which matters, because this is the part you
are most likely to want to change.
"""

import re

#: Which of Freesound's "previews" we download when OAuth2 is not in use.
#: "preview-hq-mp3" is ~128 kbps; the lq one is ~64 kbps.
PREVIEW_KEY = "preview-hq-mp3"


def search_filter(extra_filter, min_avg_rating, min_num_ratings):
    """The Freesound ``filter=`` string.

    ``extra_filter`` is any narrowing you want -- a tag, a category, a duration
    range -- or empty for "any sound". The two rating filters are appended, so
    that "highly rated" means "rated well by several people" rather than "one
    person happened to press five stars".
    """
    ratings = "avg_rating:[%s TO *] num_ratings:[%s TO *]" % (
        min_avg_rating,
        min_num_ratings,
    )
    extra_filter = (extra_filter or "").strip()
    if not extra_filter:
        return ratings
    # Parenthesise it: a multi-clause filter such as `a:1 OR b:2` would
    # otherwise let the rating thresholds drift out of the expression.
    return "(%s) %s" % (extra_filter, ratings)


def safe_name(name):
    """A version of a sound's name that is safe to put on a filesystem."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name or "").strip("._-")
    return cleaned[:80] or "sound"


def sound_path(directory, sound, extension):
    """Where one sound will be written: ``<id>_<name>.<extension>``.

    The id comes first so two sounds with the same name cannot collide.
    """
    return directory / ("%s_%s.%s" % (sound["id"], safe_name(sound.get("name")), extension))


def extension_for(sound, download_format):
    """``.wav``/``.flac``/... for originals, ``.mp3`` for previews."""
    if download_format == "original":
        return (sound.get("type") or "wav").lower()
    return "mp3"


def preview_url(sound):
    """The best MP3 preview URL, or None if Freesound did not return one."""
    previews = sound.get("previews") or {}
    return previews.get(PREVIEW_KEY) or previews.get("preview-lq-mp3")


def max_bytes_from_mb(value):
    """The byte limit for the ``MAX_DOWNLOAD_MB`` setting; ``None`` means no limit.

    Zero, empty, or unparseable all mean "no limit", so the default is to
    download whatever matches.
    """
    try:
        megabytes = float(value)
    except (TypeError, ValueError):
        return None
    if megabytes <= 0:
        return None
    return int(megabytes * 1024 * 1024)


def exceeds_max(size_bytes, max_bytes):
    """Is a *known* size over the limit? An unknown size (``None``) is never.

    Used on the search result's ``filesize``, which describes the original
    upload -- exact for original downloads, only a hint for previews, whose size
    depends on duration instead.
    """
    if max_bytes is None or size_bytes is None:
        return False
    return size_bytes > max_bytes


def keep(sound, seen_ids, min_avg_rating, min_num_ratings):
    """Should this search result be downloaded?

    Freesound already applied these filters on its side; this re-applies them to
    the response we actually received, and adds the one thing the server cannot
    know: whether we have taken this sound before.
    """
    if not isinstance(sound, dict):
        return False
    if sound.get("id") in seen_ids:
        return False
    if float(sound.get("avg_rating") or 0) < min_avg_rating:
        return False
    if int(sound.get("num_ratings") or 0) < min_num_ratings:
        return False
    return True
