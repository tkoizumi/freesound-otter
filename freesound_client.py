"""Every network call to Freesound lives in this file.

Standard library only, so there is no dependency to install.

Freesound uses *two* different credentials, and they are easy to confuse:

  * an **API key** ("token"), which authenticates read-only calls such as
    search. That is all you need to start.
  * **OAuth2**, which is required only to download a sound's original file.
    Freesound wants to know *which user* is taking the file, so the API key is
    not enough for that one endpoint.

`Freesound.search()` uses the first. `Freesound.refresh_access_token()` and
`Freesound.download(..., access_token=...)` use the second.
"""

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

#: The sound properties we ask for. Requesting exactly these keeps each search
#: response small and means one request per page instead of one per sound.
FIELDS = (
    "id",
    "name",
    "url",
    "type",
    "duration",
    "filesize",
    "license",
    "username",
    "tags",
    "avg_rating",
    "num_ratings",
    "previews",
    "download",
)

DEFAULT_API_BASE = "https://freesound.org/apiv2"


class FreesoundError(Exception):
    """A Freesound call failed, with a message worth showing a human."""


class TooLarge(FreesoundError):
    """A file is bigger than ``MAX_DOWNLOAD_MB``, so it was not downloaded.

    A subclass of ``FreesoundError`` so it can never escape as a surprise, but
    callers catch it first: being too big is a skip, not a failure.
    """

    def __init__(self, size_bytes):
        self.size_bytes = size_bytes
        super().__init__("file is %s bytes, over the limit" % size_bytes)


def _ssl_context():
    """A TLS context whose CA bundle actually exists.

    python.org's macOS installer ships no CA store, so HTTPS fails until Apple's
    "Install Certificates.command" is run. certifi is already installed beside
    that interpreter, so fall back to it rather than asking the operator to.
    """
    paths = ssl.get_default_verify_paths()
    has_default = (paths.cafile and os.path.exists(paths.cafile)) or (
        paths.capath and os.path.isdir(paths.capath)
    )
    if has_default:
        return ssl.create_default_context()
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not follow redirects automatically.

    Freesound's original-file endpoint answers with a redirect to a CDN URL.
    That URL must be fetched *without* our Authorization header, and the default
    urllib handler would forward it, so we follow the redirect ourselves in
    `download()`.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Freesound:
    """A tiny client for the three Freesound resources this integration uses."""

    def __init__(self, api_key, api_base=DEFAULT_API_BASE, timeout=60):
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        # `_opener` is for the API (it keeps 3xx responses for us to inspect);
        # `_plain_opener` is for CDN preview URLs.
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=_ssl_context()),
            _NoRedirect(),
        )
        self._plain_opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=_ssl_context())
        )

    # -- search -------------------------------------------------------------

    def search(self, query, filter_text, sort="rating_desc", page=1, page_size=50):
        """One page of search results, with the total number of matches.

        Returns the parsed response: ``{"count": int, "results": [...]}``.
        """
        params = {
            "query": query,
            "filter": filter_text,
            "sort": sort,
            "page": page,
            "page_size": page_size,
            "fields": ",".join(FIELDS),
        }
        url = "%s/search/?%s" % (self.api_base, urllib.parse.urlencode(params))
        return self._request_json(url, headers={"Authorization": "Token " + self.api_key})

    # -- OAuth2, only needed for original-quality downloads ------------------

    def refresh_access_token(self, client_id, client_secret, refresh_token):
        """Trade a refresh token for a short-lived access token.

        Returns ``(access_token, refresh_token)``. Freesound rotates the refresh
        token every time, so the caller must store the new one for next time.
        """
        body = urllib.parse.urlencode(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        ).encode("utf-8")
        payload = self._request_json(self.api_base + "/oauth2/access_token/", data=body)
        try:
            return payload["access_token"], payload["refresh_token"]
        except (KeyError, TypeError):
            raise FreesoundError("Freesound did not return a token: %r" % (payload,))

    # -- download -----------------------------------------------------------

    def download(self, url, destination, access_token=None, max_bytes=None):
        """Stream ``url`` to ``destination``.

        The bytes land in ``destination + ".part"`` first and are renamed only
        once the whole file has arrived, so a download that is interrupted never
        leaves a half-written sound behind.

        ``max_bytes`` refuses an oversized file before any of it is written, and
        abandons one whose size was not declared as soon as it passes the limit.
        ``None`` means no limit.
        """
        headers = {"Authorization": "Bearer " + access_token} if access_token else {}
        response = self._open(url, headers)

        # A redirect (returned as an HTTPError because of `_NoRedirect`) points
        # at a CDN URL, which must be fetched without the Authorization header.
        if isinstance(response, urllib.error.HTTPError):
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise FreesoundError("download redirected with no Location header")
            response = self._open_plain(location)

        # The CDN declares the exact size up front, so an oversized file can be
        # refused having transferred nothing at all.
        declared = _content_length(response)
        if max_bytes is not None and declared is not None and declared > max_bytes:
            response.close()
            raise TooLarge(declared)

        temporary = destination + ".part"
        written = 0
        try:
            with response, open(temporary, "wb") as handle:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if max_bytes is not None and written > max_bytes:
                        raise TooLarge(written)
                    handle.write(chunk)
            os.replace(temporary, destination)
        except BaseException:
            if os.path.exists(temporary):
                os.remove(temporary)
            raise

    # -- plumbing -----------------------------------------------------------

    def _request_json(self, url, headers=None, data=None):
        response = self._open(url, headers or {}, data=data)
        with response:
            try:
                return json.load(response)
            except ValueError as error:
                raise FreesoundError("Freesound returned a non-JSON response") from error

    def _open(self, url, headers, data=None):
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            return self._opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            if 300 <= error.code < 400:
                return error  # an HTTPError is also a readable response object
            raise FreesoundError(_http_error_message(error)) from error
        except urllib.error.URLError as error:
            raise FreesoundError("could not reach Freesound: %s" % (error.reason,)) from error

    def _open_plain(self, url):
        try:
            return self._plain_opener.open(url, timeout=self.timeout)
        except (urllib.error.HTTPError, urllib.error.URLError) as error:
            raise FreesoundError("could not download the file: %s" % (error,)) from error


def _content_length(response):
    """The response's declared size in bytes, or ``None`` when it does not say."""
    try:
        return int(response.headers.get("Content-Length"))
    except (TypeError, ValueError):
        return None


def _http_error_message(error):
    """Turn an HTTPError into a sentence that says what Freesound objected to."""
    detail = ""
    try:
        body = error.read().decode("utf-8", "replace")
        try:
            detail = json.loads(body).get("detail") or body
        except ValueError:
            detail = body
    except Exception:  # the body is a nicety, never a reason to fail differently
        detail = str(error)
    return "Freesound returned HTTP %s: %s" % (error.code, detail)
