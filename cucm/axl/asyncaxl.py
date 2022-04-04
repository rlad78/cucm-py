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
from zeep.helpers import serialize_object
import httpx
import logging
from logging.handlers import RotatingFileHandler

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
    def __init__(self, username: str, password: str, server: str, port: str = "8443") -> None:
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
