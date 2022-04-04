from cucm.axl.exceptions import *
from cucm.axl.configs import AXL_DIR, CUCM_LATEST_VERSION
from cucm.connection import *
import xml.etree.ElementTree as ET
import validators
from bs4 import BeautifulSoup
from pathlib import Path


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
    fullurl = generate_proper_url(url, port)
    if not validators.url(fullurl):
        raise URLInvalidError(fullurl)

    status = get_url_status_code(fullurl, timeout=10)
    if status == 200:
        with session_standard() as s:
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
    fullurl: str = generate_proper_url(ucm, port)
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


def _get_schema_versions() -> list[str]:
    schema_dir = AXL_DIR / "schema"
    if not schema_dir.is_dir():
        raise DumbProgrammerException("Cannot find schema dir")

    return [p.name for p in schema_dir.glob("*")]


def get_ucm_version(ucm_url: str, port="8443") -> str:
    """Finds the UCM version of the server.

    Parameters
    ----------
    ucm_url : str
        The base URL of the UCM server.
    port : str, optional
        The port that UCM services can be accessed at, by default "8443"

    Returns
    -------
    str
        The version number found (first two digits only)

    Raises
    ------
    UDSConnectionError
        if connection to the CUCM UDS service fails
    UDSParseError
        if the version cannot be parsed from the returned XML
    UCMVersionError
        if the version found is not supported by cucm-py
    """
    url = generate_proper_url(ucm_url, port)
    if url.endswith("/"):
        url = url[:-1]
    url += "/cucm-uds/version"

    recv = requests.get(url)
    try:
        tree = ET.fromstring(recv.text)
    except (TypeError, ET.ParseError):
        raise UDSConnectionError(url)

    raw_version = tree.get("version", None)
    if raw_version is None:
        raise UDSParseError(url, "version", recv.text)

    concise_version = ".".join(raw_version.split(".")[:2])
    if concise_version in _get_schema_versions():
        return concise_version
    elif concise_version == CUCM_LATEST_VERSION:
        return "current"
    else:
        raise UCMVersionError(ucm_url, concise_version)
