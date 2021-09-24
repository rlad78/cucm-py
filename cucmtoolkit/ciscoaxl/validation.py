from cucmtoolkit.ciscoaxl.exceptions import *
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
import validators
from bs4 import BeautifulSoup


def _session() -> requests.Session:
    s = requests.Session()
    s.headers = "Mozilla/5.0 (X11; CrOS x86_64 12871.102.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.141 Safari/537.36"
    retry_strat = Retry(
        read=3,
        connect=3,
        status=3,
        other=3,
        total=5,
        backoff_factor=0.1,
    )
    s.mount("http://", HTTPAdapter(max_retries=retry_strat))
    return s


def _session_auth(username: str, password: str) -> requests.Session:
    s = _session()
    s.auth = HTTPBasicAuth(username, password)
    return s


def _generate_proper_url(url: str, port=0) -> str:
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    url_parts = urlparse(url)
    scheme = url_parts.scheme
    netloc = url_parts.netloc
    urlpath = url_parts.path

    # subdomain, domain, suffix = tldextract(url)
    if port == 0:
        return f"{scheme}://{netloc}{urlpath}"
    else:
        return f"{scheme}://{netloc}:{port}{urlpath}"


def _get_base_url(url: str) -> str:
    return ".".join(tldextract.extract(_generate_proper_url(url)))


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
        sesh = _session_auth(username, password)
    else:
        sesh = _session()

    try:
        with sesh as s:
            return s.get(url, stream=True, timeout=timeout).status_code
    except (ConnectionError, MaxRetryError, TimeoutError):
        return -1
    except ConnectTimeout:
        return 0


def validate_ucm_server(url: str, port="8443") -> bool:
    """Checks to see if the given url-port combination leads to
    a valid UCM server.

    Args:
        url (str): Root URL of UCM server.
        port (int, optional): Port of UCM server. Defaults to "8443".

    Raises:
        URLInvalidError: when an invalid/malformed URL is given.
        UCMInvalidError: when the URL does not point to a UCM server.
        UCMNotFoundError: when the URL given is not reachable
        UCMConnectionFailure: when accessing the URL fails

    Returns:
        bool: True if connected to valid UCM server, False otherwise
    """
    fullurl = _generate_proper_url(url, port)
    if not validators.url(fullurl):
        raise URLInvalidError(fullurl)

    status = get_url_status_code(fullurl, timeout=10)
    if status == 200:
        with _session() as s:
            if not BeautifulSoup(s.get(fullurl, timeout=10).text, "html.parser").find(
                string="Cisco Unified Communications Manager"
            ):
                raise UCMInvalidError(fullurl)
        return True
    elif status == -1:
        raise UCMNotFoundError(fullurl)
    elif status == 0:
        raise UCMConnectionFailure(fullurl)
    else:
        return False


def validate_axl_auth(ucm: str, username: str, password: str, port="8443") -> bool:
    """Checks to see if the AXL API is accessible from the given UCM server.

    Args:
        ucm (str): URL of UCM server.
        username (str): Administrator (with AXL access) account username
        password (str): Administrator account password
        port (str, optional): Port of UCM server. Defaults to "8443".

    Raises:
        URLInvalidError: when an invalid/malformed URL is given.
        AXLInvalidCredentials: when the AXL API is reachable,
            but the credentials are rejected.
        AXLNotFoundError: when the URL given is not reachable
        AXLConnectionFailure: when accessing the URL fails

    Returns:
        bool: True if AXL is reachable, False otherwise.
    """
    fullurl: str = _generate_proper_url(ucm, port)
    if not all((username, password)):
        return False

    if fullurl.endswith("/"):
        fullurl += "axl/"
    else:
        fullurl += "/axl/"

    if not validators.url(fullurl):
        raise URLInvalidError(fullurl)

    status = get_url_status_code(fullurl, username, password, timeout=3)
    if status == 200:
        return True
    elif status == 401:
        raise AXLInvalidCredentials(fullurl, username)
    elif status == -1:
        raise AXLNotFoundError(fullurl)
    elif status == 0:
        raise AXLConnectionFailure(fullurl)
    else:
        return False
