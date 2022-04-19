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
from cucm.axl.wsdl import AXLElement, get_tree
from zeep import AsyncClient, Settings
from zeep.client import AsyncServiceProxy
from zeep.transports import AsyncTransport
from zeep.xsd import Nil
from zeep.exceptions import Fault
import httpx
import logging
from logging.handlers import RotatingFileHandler
import re
from enum import Enum, unique
from collections import defaultdict
from copy import copy

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
TASK_COUNT_LOCK = asyncio.Lock()
PHONE_MANIP_LOCKS = defaultdict(asyncio.Lock)


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

    #################################
    # ==== TEMPLATES & HELPERS ==== #
    #################################

    async def _generic_soap_call(
        self,
        element: str,
        action: APICall,
        children: list[str] = None,
        task_number: int = None,
        **kwargs,
    ) -> Union[object, dict, list]:
        if (func := getattr(self.aclient, element, None)) is None:
            raise DumbProgrammerException(f"{element} is not an AXL element")
        if children is None:
            children = []

        if task_number is not None:
            current_task = f"[{str(task_number).zfill(4)}]"
        else:
            current_task = f"[{str(await checkout_task()).zfill(4)}]"

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
        task_number: int = None,
        **kwargs,
    ):
        """Same as _generic_soap_call, but looks for 'uuid' and the value of 'base_field' in the supplied kwargs. If a UUID exists, it will use this value in the SOAP call over the 'base_field' value.

        Args:
            element (str): Name of the element being used for the call i.e. getPhone
            action (APICall): Type of call being made (usually the prefix of the element)
            base_field (str): Key used in kwargs as the primary identifier (i.e. 'name', 'mac', etc.)
            children (list[str], optional): Keys of the returned nested object that lead to the desired information. Defaults to None.

        Raises:
            DumbProgrammerException: When 'base_field' doesn't exist in kwargs
            InvalidArguments: When the values for 'base_field' and 'uuid' are both empty

        Returns:
            zeep.object: When valid data is found (@serialize will turn this into a dict)
            dict: Empty dict when GET call is successful but no data is found
            list: Empty list when LIST call is successful but no items are returned
        """
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

        return await self._generic_soap_call(
            element, action, children, task_number=task_number, **kwargs
        )

    async def _generic_soap_add(
        self, element: str, adding_element: str, task_number: int = None, **kwargs
    ) -> str:
        results = self._generic_soap_call(
            element, APICall.ADD, task_number=task_number, **{adding_element: kwargs}
        )
        if results and (info := results.get("return", None)) is not None:
            return info.get("UUID", "")
        else:
            return ""

    async def _make_calls(self, method: str, kwargs_list: list[dict]) -> list[dict]:
        if (func := getattr(self, method, None)) is None:
            raise DumbProgrammerException(f"{method} is not an AsyncAXL method")

        log.debug(f"Starting {len(kwargs_list)} {method} tasks...")
        call_tasks = [asyncio.create_task(func(**kw)) for kw in kwargs_list]
        results = await asyncio.gather(*call_tasks)
        log.debug(
            f"Finished {len(kwargs_list)} {method} tasks, returned {len([x for x in results if x])} items"
        )
        return results

    def __extract_template(self, element_name: str, template: dict, child="") -> dict:
        """Removes all unnecessary values from a device/line/etc template, like None and "" values. Keeps any values that are required by the given element_name, regardless of what the values are.

        Args:
            element_name (str): The 'get' element used to get the template
            template (dict): UCM template data
            child (str, optional): Used as a key for the template dict. Use "" to ignore. Defaults to "".

        Returns:
            dict: Template data removed of unnecessary values
        """

        def is_removable(branch: dict) -> bool:
            for value in branch.values():
                if type(value) == dict:
                    if is_removable(value) == False:
                        return False
                elif type(value) == list:
                    if not all([type(v) == dict for v in value]):
                        return False
                    elif not all([is_removable(v) for v in value]):
                        return False
                elif value not in (None, -1, ""):
                    return False
            else:
                return True

        def tree_match(root: AXLElement, template: dict) -> dict:
            result = {}
            for name, value in template.items():
                if (node := root.get(name, None)) is not None:
                    if node.children:
                        if type(value) == dict:
                            result_dict = tree_match(node, value)
                            if (
                                not is_removable(result_dict)
                                or node._parent_chain_required()
                            ):
                                result[name] = result_dict
                        elif type(value) == list:
                            result_list = [tree_match(node, t) for t in value]
                            if all([type(r) == dict for r in result_list]):
                                result_list = [
                                    r for r in result_list if not is_removable(r)
                                ]
                            if result_list:
                                result[name] = result_list
                    elif value is None and node._parent_chain_required():
                        result[name] = Nil
                    elif value not in (None, -1, "") or node._parent_chain_required():
                        # else:
                        result[name] = value
            return result

        tree: AXLElement = get_tree(self.zeep, element_name)
        if child:
            tree = tree.get(child, None)
            if tree is None:
                raise DumbProgrammerException(
                    f"{tree.name} does not have a child named '{child}'"
                )

        result_data = tree_match(tree, template)
        for name, value in deepcopy(result_data).items():
            if tree.get(name)._parent_chain_required():
                # continue
                if value is None:
                    result_data[name] = Nil
            elif value in (None, -1, ""):
                result_data.pop(name)
            elif type(value) == dict and is_removable(value):
                result_data.pop(name)

        return result_data

    async def _from_phone_template(self, template_name: str, **kwargs) -> dict:
        """Generates template data from a given UCM phone template. The template data can be used as a base to insert new phones.

        Args:
            template_name (str): The name of a phone template in Bulk Administration -> Phones -> Phone Template

        Returns:
            dict: The parsed template data
        """
        template_data = await self.get_phone(name=template_name)
        if not template_data:
            raise InvalidArguments(f"Phone template '{template_name}' was not found")
        template_data.update({"class": "Phone"}, **kwargs)
        for value in ("lines", "loadInformation", "versionStamp"):
            if value in template_data:
                del template_data[value]

        result = self.__extract_template("addPhone", template_data, "phone")
        return result

    async def _from_line_template(
        self, template_name: str, template_route_partition: str, **kwargs
    ) -> dict:
        """Generates template data from a given UCM line template. The template data can be used as a base to insert new lines.

        Args:
            template_name (str): The name of a line template from one of the phone templates in Bulk Administration -> Phones -> Phone Template
            template_route_partition (str): The route partition of the template line

        Returns:
            dict: The parsed template data
        """
        template_data = await self.get_directory_number(
            pattern=template_name,
            route_partition=template_route_partition,
        )
        template_data.update({"active": "true", "usage": Nil}, **kwargs)
        return self.__extract_template("addLine", template_data, "line")

    ##################
    # ==== GETs ==== #
    ##################

    @serialize
    @check_tags("getPhone")
    async def get_phone(
        self, name: str = "", uuid: str = "", *, return_tags: list[str] = None
    ) -> dict:
        """Attemps to retrieve the phone device with the given 'name' or 'uuid'. Returns an empty dict if the device is not found.

        Args:
            name (str, optional): Name of the device, including the prefix (SEP, AN, etc). Defaults to "".
            uuid (str, optional): UUID of the device. Can be found via other AXL calls. Defaults to "".
            return_tags (list[str], optional): Tags to choose what data will be returned. Leave as None to return all tags. Defaults to None.

        Returns:
            dict: Phone data, empty if phone isn't found.
        """
        return await self._generic_soap_with_uuid(
            "getPhone",
            APICall.GET,
            "name",
            ["return", "phone"],
            name=name,
            uuid=uuid,
            returnedTags=return_tags,
        )

    @serialize
    @check_tags("getPhone")
    async def get_phones(
        self,
        names: list[str] = None,
        uuids: list[str] = None,
        *,
        return_tags: list[str] = None,
    ) -> list[dict]:
        if names:
            return await self._make_calls(
                "get_phone", [{"name": n, "returnedTags": return_tags} for n in names]
            )
        elif uuids:
            return await self._make_calls(
                "get_phone", [{"uuid": u, "returnedTags": return_tags} for u in uuids]
            )
        else:
            raise InvalidArguments("Neither names nor uuids were supplied")

    @serialize
    @check_tags("listPhone")
    async def find_phones(
        self,
        name_search: str = "",
        desc_search: str = "",
        css_search: str = "",
        pool_search: str = "",
        security_profile_search: str = "",
        *,
        return_tags: list[str] = None,
    ) -> list[dict]:
        args_dict = {
            "name": name_search,
            "description": desc_search,
            "callingSearchSpaceName": css_search,
            "devicePoolName": pool_search,
            "securityProfileName": security_profile_search,
        }
        args_dict = {k: v for k, v in args_dict.items() if v}
        if len(args_dict) == 0:
            raise InvalidArguments("No search query supplied")
        args_dict["returnedTags"] = return_tags

        return await self._generic_soap_call("listPhone", APICall.LIST, **args_dict)

    @serialize
    async def get_phone_lines(self, name: str = "", uuid: str = "") -> list[dict]:
        tags = fix_return_tags(self.zeep, "getPhone", ["lines"])
        result = await self._generic_soap_with_uuid(
            "getPhone", APICall.GET, "name", ["return", "phone", "lines", "line"], name=name, uuid=uuid, returnedTags=tags
        )
        if not result:
            return []  # adjust for empty dict returned
        else:
            return result

    @serialize
    @check_tags("getLine")
    async def get_directory_number(
        self,
        pattern: str = "",
        route_partition: str = "",
        uuid: str = "",
        *,
        return_tags: list[str] = None,
    ) -> dict:
        """Attempts to retrieve the directory number with the given 'pattern' and 'route_partition', or with the given 'uuid'. Returns an empty dict if the DN isn't found.

        Args:
            pattern (str, optional): The pattern (usually phone number) of the DN. Defaults to "".
            route_partition (str, optional): The Route Partition to search in. Defaults to "".
            uuid (str, optional): The DN's UUID. Can be found via other AXL calls. Defaults to "".
            return_tags (list[str], optional): Tags to choose what data will be returned. Leave as None to return all tags. Defaults to None.

        Raises:
            InvalidArguments: When neither pattern and route_partition, nor uuid are supplied.

        Returns:
            dict: DN data, empty if DN isn't found
        """
        args_pack = {"returnedTags": return_tags}
        if pattern and route_partition:
            args_pack.update(
                {
                    "pattern": pattern,
                    "routePartitionName": route_partition,
                }
            )
        elif uuid:
            args_pack.update(
                {
                    "uuid": uuid,
                }
            )
        else:
            raise InvalidArguments(
                "Both pattern and route_partition need valid argument values"
            )

        return await self._generic_soap_call(
            "getLine",
            APICall.GET,
            ["return", "line"],
            **args_pack,
        )

    @serialize
    @check_tags("getLine")
    async def get_directory_numbers(
        self,
        dn_list: list[tuple[str, str]] = None,
        uuids: list[str] = None,
        *,
        return_tags: list[str] = None,
    ) -> list[dict]:
        if dn_list:
            try:
                args_list = [
                    {"pattern": dn[0], "route_partition": dn[1]} for dn in dn_list
                ]
            except (TypeError, KeyError):
                raise InvalidArguments(
                    "dn_list must be tuple pairs of ('pattern', 'route_partition')"
                )
        elif uuids:
            args_list = [{"uuid": u} for u in uuids]

        for args in args_list:
            args["returnedTags"] = return_tags

        return await self._make_calls("get_directory_number", args_list)

    @serialize
    @check_tags("listLine")
    async def find_directory_numbers(
        self,
        pattern_search: str = "",
        route_partition_search: str = "",
        desc_search: str = "",
        *,
        return_tags: list[str] = None,
    ) -> list[dict]:
        args_pack = {
            "pattern": pattern_search,
            "routePartitionName": route_partition_search,
            "description": desc_search,
        }
        args_pack = {k: v for k, v in args_pack.items() if v}

        if not args_pack:
            raise InvalidArguments("No search query supplied")
        args_pack["returnedTags"] = return_tags

        return await self._generic_soap_call(
            "listLine", APICall.LIST, ["return", "line"], **args_pack
        )

    ##################
    # ==== ADDs ==== #
    ##################

    @check_arguments("addPhone")
    async def add_phone(
        self,
        name: str,
        description: str,
        model: str,
        device_pool: str,
        button_template: str,
        protocol="SIP",
        common_phone_profile="Standard Common Phone Profile",
        location="Hub_None",
        use_trusted_relay_point: Union[str, bool] = "Default",
        built_in_bridge="Default",
        packet_capture_mode="None",
        mobility_mode="Default",
        **kwargs,
    ) -> str:
        phone_details = {
            "name": name,
            "description": description,
            "product": model,
            "class": "Phone",
            "protocol": protocol,
            "protocolSide": "User",
            "devicePoolName": device_pool,
            "commonPhoneConfigName": common_phone_profile,
            "locationName": location,
            "useTrustedRelayPoint": use_trusted_relay_point,
            "phoneTemplateName": button_template,
            "primaryPhoneName": Nil,
            "builtInBridgeStatus": built_in_bridge,
            "packetCaptureMode": packet_capture_mode,
            "certificateOperation": "No Pending Operation",
            "deviceMobilityMode": mobility_mode,
            **kwargs,
        }
        return await self._generic_soap_add("addPhone", "phone", **phone_details)

    @check_arguments("addPhone")
    async def add_phone_from_template(
        self,
        name: str,
        description: str,
        model: str,
        phone_template: str,
        **kwargs,
    ) -> str:
        template_data = await self._from_phone_template(
            phone_template, name=name, description=description, product=model, **kwargs
        )
        return await self._generic_soap_add("addPhone", "phone", **template_data)

    @check_arguments("addLine")
    async def add_directory_number(
        self, pattern: str, route_partition: str, **kwargs
    ) -> str:
        return await self._generic_soap_add(
            "addLine",
            "line",
            pattern=pattern,
            routePartitionName=route_partition,
            **kwargs,
        )

    @check_arguments("addLine")
    async def add_directory_number_from_template(
        self,
        pattern: str,
        route_partition: str,
        line_template: str,
        line_template_partition: str,
        **kwargs,
    ) -> str:
        template_data = await self._from_line_template(
            line_template,
            line_template_partition,
            pattern=pattern,
            routePartitionName=route_partition,
            **kwargs,
        )
        return await self._generic_soap_add("addLine", "line", **template_data)

    async def add_phone_line(
        self,
        pattern: str,
        route_partition: str,
        phone_name: str = "",
        phone_uuid: str = "",
        *,
        line_index: int = -1,
    ) -> None:
        this_task = await checkout_task()

        if phone_uuid and not phone_name:
            # if the uuid is given, we have to get the name for async locks,
            # then get the device again after the asyncio lock to make sure we
            # have the latest line config
            device = await self.get_phone(uuid=phone_uuid, return_tags=["name"])
            if (phone_name := device.get("name", None)) is None:
                raise InvalidArguments(f"{phone_uuid=} does not lead to a valid phone")

        sem = PHONE_MANIP_SEM[phone_name]
        if sem.locked():
            log.debug(f"Waiting on locked resource for phone '{phone_name}'")

        async with sem:
            log.debug(f"Locking resources for phone '{phone_name}'...")
            # while we're looking for the phone, make sure the DN exists
            find_dn = asyncio.create_task(
                self.get_directory_number(
                    pattern,
                    route_partition,
                    return_tags=["pattern", "routePartitionName"],
                )
            )

            device = await self.get_phone(name=phone_name, return_tags=["lines"])
            if (original_lines := device.get("lines", "MISSING")) == "MISSING":
                raise InvalidArguments(f"Phone '{phone_name}' could not be found")
            elif original_lines is None:
                original_lines = []
            else:
                original_lines = original_lines["line"]

            dn = await find_dn
            if not dn:
                raise InvalidArguments(
                    f"DN with {pattern=} and {route_partition=} could not be found. Does it exist?"
                )

            new_dn = {
                "directoryNumber": pattern,
                "routePartitionName": route_partition,
            }
            new_lines = [
                {
                    "directoryNumber": dn["dirn"]["pattern"],
                    "routePartitionName": dn["dirn"]["routePartitionName"],
                }
                for dn in original_lines
            ]
            if line_index < 0 or line_index > len(original_lines):
                new_lines.append(new_dn)
                log.info(
                    f"[{str(this_task).zfill(4)}] Adding Line ({pattern}, {route_partition}) to Phone '{phone_name}'..."
                )
            else:
                new_lines.insert(line_index, new_dn)
                log.info(
                    f"[{str(this_task).zfill(4)}] Adding Line ({pattern}, {route_partition}) to Phone '{phone_name}' at position {line_index}..."
                )

            await self._generic_soap_call(
                "updatePhone",
                APICall.UPDATE,
                task_number=this_task,
                name=phone_name,
                lines={"line": new_lines},
            )
            log.info(f"[{str(this_task).zfill(4)}] Line added!")

        log.debug(f"Released resources for phone '{phone_name}'")


async def checkout_task() -> int:
    global TASK_COUNT_LOCK
    global TASK_COUNTER

    lock = TASK_COUNT_LOCK
    async with lock:
        TASK_COUNTER += 1
        return copy(TASK_COUNTER)

def task_string(task_number: int) -> str:
    return f"[{str(task_number).zfill(4)}]"

async def checkout_task_str() -> str:
    task_number = await checkout_task()
    return task_string(task_number)