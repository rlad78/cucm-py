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
from enum import Enum, unique

# LOGGING SETTINGS
logdir = rootcfg.LOG_DIR
if not logdir.is_dir():
    logdir.mkdir(exist_ok=True)
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)
f_format = logging.Formatter(
    "%(asctime)s [%(levelname)s]:%(name)s:%(funcName)s - %(message)s"
)
s_format = logging.Formatter("[%(levelname)s]:%(name)s:%(funcName)s - %(message)s")
f_handler = RotatingFileHandler(
    rootcfg.LOG_DIR / f"{__name__}.log",
    maxBytes=(1024 * 1024 * 5),
    backupCount=3,
)
f_handler.setLevel(logging.DEBUG)
f_handler.setFormatter(f_format)
s_handler = logging.StreamHandler()
s_handler.setLevel(logging.WARNING)
s_handler.setFormatter(s_format)
log.addHandler(f_handler)
log.addHandler(s_handler)
log.info(f"----- NEW {__name__} SESSION -----")


@unique
class APICall(Enum):
    GET = "GET"
    ADD = "ADD"
    LIST = "LIST"
    UPDATE = "UPDATE"
    DO = "DO"
    REMOVE = "REMOVE"
    RESTART = "RESTART"
    RESET = "RESET"
    WIPE = "WIPE"


TASK_COUNTER = 0


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
    """An asynchronous interface for the AXL API."""

    def __init__(
        self,
        username: str,
        password: str,
        server: str,
        port: str = "8443",
        *,
        version: str = None,
    ) -> None:
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
        log.info(
            f"Attempting to verify {server} on port {port} is a valid UCM server..."
        )
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
                log.debug(
                    f"Supplied UCM version '{parsed_version}' didn't have a decimal place, adding '.0' to end."
                )
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
        self.zeep = AsyncClientExt(
            str(wsdl_path),
            settings=settings,
            transport=AsyncTransport(client=httpx_client),
        )
        self.aclient = self.zeep.create_service(
            "{http://www.cisco.com/AXLAPIService/}AXLAPIBinding",
            f"https://{server}:{port}/axl/",
        )
        log.info(f"AXL Async client created for {server}")

    ###############################
    # ==== TEMPLATES & HELPERS ====
    ###############################

    async def _generic_soap_call(
        self, element: str, action: APICall, children: list[str] = None, **kwargs
    ):
        """Base logic for making a call to the AXL SOAP API. Used when no other logic is needed to perform the required action.

        Args:
            element (str): Name of the element being used for the call, i.e. getPhone
            action (APICall): Type of call being made (usually the prefix of the element)
            children (list[str], optional): Keys of the returned nested object that lead to the desired information. Defaults to None.

        Returns:
            zeep.object: When valid data is found (@serialize will turn this into a dict)
            dict: Empty dict when GET call is successful but no data is found
            list: Empty list when LIST call is successful but no items are returned
        """
        if (func := getattr(self.aclient, element, None)) is None:
            raise DumbProgrammerException(f"{element} is not an AXL element")
        if children is None:
            children = []

        global TASK_COUNTER
        TASK_COUNTER += 1
        current_task = f"[{str(TASK_COUNTER).zfill(4)}]"

        log.info(f"{current_task} Performing {action.value} for {kwargs}")
        try:
            results = await func(**kwargs)
        except Fault as e:
            if action is APICall.GET:
                if "was not found" in e.message:
                    log.info(f"{current_task} Completed, but could not find item")
                    return {}
                else:
                    log.exception()
                    raise
            elif action is APICall.LIST:
                log.info(f"{current_task} Completed, but list empty")
                return []
            else:
                log.exception()
                raise e

        try:
            for child in children:
                results = results[child]
        except KeyError:
            raise DumbProgrammerException(f"Invalid children for {element}: {children}")
        except TypeError:
            if action is APICall.GET:
                log.info(f"{current_task} Completed, but no item was returned")
                return {}
            elif action is APICall.LIST:
                log.info(f"{current_task} Completed, but no items were returned")
                return []
            else:
                raise DumbProgrammerException(
                    f"Children {children} lead to 'None' result"
                )

        log.info(f"{current_task} Completed successfully")
        return results

    async def _generic_soap_with_uuid(
        self,
        element: str,
        action: APICall,
        base_field: str,
        children: list[str] = None,
        **kwargs,
    ):
        if base_field not in kwargs.keys() or "uuid" not in kwargs.keys():
            raise DumbProgrammerException(
                f"'{base_field}' was not a provided field in kwargs"
            )

        uuid = kwargs["uuid"]
        base = kwargs[base_field]
        if base and not uuid:
            kwargs.pop("uuid")
        elif uuid:
            kwargs.pop(base_field)
        else:
            raise InvalidArguments(f"No {base_field} or uuid values found in kwargs")

        return await self._generic_soap_call(element, action, children, **kwargs)

    @serialize
    @check_tags("getPhone")
    async def get_phone(self, name: str, *, return_tags: list[str] = None) -> dict:
        return await self._generic_soap_call(
            "getPhone",
            APICall.GET,
            ["return", "phone"],
            name=name,
            returnedTags=return_tags,
        )
