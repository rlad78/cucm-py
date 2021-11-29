import requests
from requests.adapters import (
    HTTPAdapter,
    ConnectTimeout,
    ConnectionError,
    MaxRetryError,
)
from requests.auth import HTTPBasicAuth
from urllib.parse import urlparse
from urllib3.util.retry import Retry
import tldextract


def session_standard() -> requests.Session:
    s = requests.Session()
    s.headers = "Mozilla/5.0 (X11; CrOS x86_64 12871.102.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.141 Safari/537.36"
    retry_strat = Retry(
        read=3,
        connect=3,
        status=3,
        # other=3,
        total=5,
        backoff_factor=0.1,
    )
    s.mount("http://", HTTPAdapter(max_retries=retry_strat))
    return s


def session_auth(username: str, password: str) -> requests.Session:
    s = session_standard()
    s.auth = HTTPBasicAuth(username, password)
    return s


def generate_proper_url(url: str, port="0") -> str:
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    url_parts = urlparse(url)
    scheme = url_parts.scheme
    netloc = url_parts.netloc
    urlpath = url_parts.path

    # subdomain, domain, suffix = tldextract(url)
    if port == "0":
        return f"{scheme}://{netloc}{urlpath}"
    else:
        return f"{scheme}://{netloc}:{port}{urlpath}"


def get_base_url(url: str) -> str:
    return ".".join(tldextract.extract(generate_proper_url(url)))


def get_url_status_code(url: str, username="", password="", timeout=10) -> int:
    """Returns HTTP status code of request with HTTP basic auth

    Args:
        url (str): Address to send request to
        username (str, optional): HTTP auth username. Defaults to "".
        password (str, optional): HTTP auth password. Defaults to "".
        timeout (int, optional): Time until request gives up. Defaults to 10.

    Returns:
        int: If cannot connect, returns -1.
            If timeout occurs, returns 0.
            Otherwise, returns request's HTTP status code
    """
    if any((username, password)):
        sesh = session_auth(username, password)
    else:
        sesh = session_standard()

    try:
        with sesh as s:
            return s.get(url, stream=True, timeout=timeout).status_code
    except (ConnectionError, MaxRetryError, TimeoutError):
        return -1
    except ConnectTimeout:
        return 0