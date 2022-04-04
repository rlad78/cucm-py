import asyncio
import cucm.axl.configs as cfg
import cucm.configs as rootcfg
from cucm.axl.helpers import *
from cucm.axl.exceptions import *
from cucm.axl.validation import (
    validate_ucm_server,
    validate_axl_auth,
    get_ucm_version,
)
from zeep import AsyncClient, Settings
from zeep.client import AsyncServiceProxy
from zeep.transports import AsyncTransport
from zeep.exceptions import Fault
import httpx
import logging
from logging.handlers import RotatingFileHandler
import re

# LOGGING SETTINGS
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)
f_format = logging.Formatter("%(asctime)s [%(levelname)s]:%(name)s:%(funcName)s - %(message)s")
s_format = logging.Formatter("[%(levelname)s]:%(name)s:%(funcName)s - %(message)s")
f_handler = RotatingFileHandler(rootcfg.LOG_DIR / f"{__name__}.log", maxBytes=(1024*1024*5), backupCount=3)
f_handler.setLevel(logging.DEBUG)
f_handler.setFormatter(f_format)
s_handler = logging.StreamHandler()
s_handler.setLevel(logging.WARNING)
s_handler.setFormatter(s_format)
log.addHandler(f_handler)
log.addHandler(s_handler)
log.info(f"----- NEW {__name__} SESSION -----")

class AsyncClientExt(AsyncClient):
    """Extended zeep.AsyncClient with upstream fix yet to be merged.
    When change at https://github.com/mvantellingen/python-zeep/pull/1202 is merged, this should no longer be needed.
    """
    def create_service(self, binding_name, address):
        """Create a new AsyncServiceProxy for the given binding name and address.
        :param binding_name: The QName of the binding
        :param address: The address of the endpoint
        Based on unmerged change https://github.com/mvantellingen/python-zeep/pull/1202
        """
        try:
            binding = self.wsdl.bindings[binding_name]
        except KeyError:
            raise ValueError(
                "No binding found with the given QName. Available bindings "
                "are: %s" % (", ".join(self.wsdl.bindings.keys()))
            )
        return AsyncServiceProxy(self, binding, address=address)


class AsyncAXL:
    """An asynchronous interface for the AXL API.
    """
    def __init__(self, username: str, password: str, server: str, port: str = "8443", *, version: str = None) -> None:
        """Connect to your UCM's AXL server.

        Args:
            username (str): A user with AXL permissions
            password (str): The password of the user
            server (str): The base URL of your UCM server, no 'http://' or 'https://' needed
            port (str, optional): The port at which UCM can be accessed. Defaults to "8443".

        Raises:
            URLInvalidError: An invalid URL for 'server' was provided
            UCMInvalidError: The 'server' address does not point to a UCM server
            UCMConnectionFailure: The connection to 'server' timed out or could not be completed
            UCMNotFoundError: The 'server' URL could not be resolved
            ConnectionError: An unknown error is preventing a connection to the UCM server
            UDSConnectionError: Cannot connect to the UCM's UDS service. If UDS is not active, please supply your UCM version e.g. 'version=11.5'
            UDSParseError: Could not parse the UCM version number from UDS. Please contact the maintainer of this project if you get this exception
            UCMVersionError: The UCM version is either invalid or unsupported
            UCMVersionInvalid: An unsupported UCV version is detected
            AXLInvalidCredentials: 
            AXLConnectionFailure: 
            AXLNotFoundError: 
            AXLException: An unknown error is preventing a connection to the AXL server
        """
        log.info(f"Attempting to verify {server} on port {port} is a valid UCM server...")
        try:
            ucm_is_valid: bool = validate_ucm_server(server, port)
        except (
            URLInvalidError,
            UCMInvalidError,
            UCMConnectionFailure,
            UCMNotFoundError,
        ) as err:
            log.exception(f"{server} failed validation tests.")
            raise
        if not ucm_is_valid:
            log.error(f"Could not connect to {server}, unknown error occured")
            raise ConnectionError()
        
        if version is not None:
            if (match := re.search(r"^(\d{1,2}(?:\.\d{1})?)", version)) is None:
                raise InvalidArguments(f"{version=} is not a valid UCM version")
            parsed_version = match.group(0)
            log.debug(f"{version=}, {parsed_version=}")
            if "." not in parsed_version:
                log.debug(f"Supplied UCM version '{parsed_version}' didn't have a decimal place, adding '.0' to end.")
                parsed_version += ".0"
            cucm_version = parsed_version
            log.debug(f"Using user supplied version '{parsed_version}'")
        else:
            try:
                cucm_version = get_ucm_version(server, port)
            except (UDSConnectionError, UDSParseError, UCMVersionError) as err:
                log.exception(err)
                raise
            log.debug(f"Found UCM version: {cucm_version}")
        
        wsdl_path = cfg.AXL_DIR / "schema" / cucm_version / "AXLAPI.wsdl"
        log.debug(f"WSDL Path: {wsdl_path}")
        if not wsdl_path.parent.is_dir():
            log.critical(f"A schema for CUCM {cucm_version} is not available")
            raise UCMVersionInvalid(cucm_version)

        log.info(f"Validating AXL credentials...")
        try:
            axl_is_valid = validate_axl_auth(server, username, password, port)
        except (AXLInvalidCredentials, AXLConnectionFailure, AXLNotFoundError) as err:
            log.exception(err)
            raise
        if not axl_is_valid:
            log.error("Could not connect to the AXL API for an unknown reason")
            raise AXLException()
        
        httpx_client = httpx.AsyncClient(auth=(username, password))
        settings = Settings(
            strict=False, xml_huge_tree=True, xsd_ignore_sequence_order=True
        )
        self.zeep = AsyncClientExt(str(wsdl_path), settings=settings, transport=AsyncTransport(client=httpx_client))
        self.aclient = self.zeep.create_service(
            "{http://www.cisco.com/AXLAPIService/}AXLAPIBinding",
            f"https://{server}:{port}/axl/",
        )
        log.info(f"AXL Async client created for {server}")

    @serialize
    @check_tags("getPhone")
    async def get_phone(self, name: str, *, return_tags: list[str] = None) -> dict:
        tags = _tag_handler(return_tags)
        try:
            result = await self.aclient.getPhone(name=name, returnedTags=tags)
            return result["return"]['phone']
        except (Fault, KeyError):
            return {}



def _tag_handler(tags: list) -> dict:
    """Internal function for handling basic and complex return tag lists. Do not use.

    Parameters
    ----------
    tags : list
        A list of str tag names, or a list containing a single dict of all tags

    Returns
    -------
    dict
        A dict with properly formatted tags for Zeep
    """
    if tags and type(tags[0]) == dict:
        return tags[0]
    elif all([bool(type(t) == str) for t in tags]):
        return {t: "" for t in tags}
