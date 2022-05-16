import asyncio
from functools import partial
from unicodedata import name
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
from collections import defaultdict, namedtuple
from copy import copy
from itertools import chain

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


CheckResults = namedtuple("CheckResults", ("found", "missing"))

# SYNC PRIMITIVES
TASK_COUNTER = 0
TASK_COUNT_LOCK = asyncio.Lock()
PHONE_MANIP_LOCKS = defaultdict(asyncio.Lock)
USER_MANIP_LOCKS = defaultdict(asyncio.Lock)
ANTI_THROTTLE_SEM_TOTAL = 20
ANTI_THROTTLE_SEM = asyncio.Semaphore(ANTI_THROTTLE_SEM_TOTAL)
ANTI_THROTTLE_TIMEOUT = 10.0
ANTI_THROTTLE_ADJUST_LOCK = asyncio.Lock()


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
        port="8443",
        *,
        version: str = None,
    ) -> None:
        """Connect to your UCM's AXL server.

        :param username: A user with AXL permissions
        :param password: Password for the given user
        :param server: Base URL for your UCM server (i.e. 'ucm.company.com')
        :param port: Port on the server where UCM can be accessed, defaults to "8443"
        :param version: Optional, use only when there is an issue determining your UCM version, defaults to None
        """
        # * server verification
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

        # * supported version check
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

        # * load schema
        wsdl_path = cfg.AXL_DIR / "schema" / cucm_version / "AXLAPI.wsdl"
        log.debug(f"WSDL Path: {wsdl_path}")
        if not wsdl_path.parent.is_dir():
            log.critical(f"A schema for CUCM {cucm_version} is not available")
            raise UCMVersionInvalid(cucm_version)

        # * validate permissions
        log.info(f"Validating AXL credentials...")
        try:
            axl_is_valid = validate_axl_auth(server, username, password, port)
        except (AXLInvalidCredentials, AXLConnectionFailure, AXLNotFoundError) as err:
            log.exception(err)
            raise
        if not axl_is_valid:
            log.error("Could not connect to the AXL API for an unknown reason")
            raise AXLException()

        # * create zeep client
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
        """Performs a SOAP call to the given `element`. Keeps track of current async calls
         and holds back calls in queue in order to prevent overwhelming AXL.

        :param element: SOAP element to be called
        :param action: Type of action to be performed (usually the prefix of `element`)
        :param children: Children of expected response that drill down to the actual data, defaults to None
        :param task_number: Override the logged task number, useful for batch operations, defaults to None
        :param kwargs: Fields added to the SOAP call
        :return: The Zeep object(s), or an empty list/dict depending on the type of `action`
        """
        if (func := getattr(self.aclient, element, None)) is None:
            raise DumbProgrammerException(f"{element} is not an AXL element")
        if children is None:
            children = []

        if task_number is not None:
            current_task = f"[{str(task_number).zfill(4)}]"
        else:
            current_task = f"[{str(await checkout_task()).zfill(4)}]"

        async with ANTI_THROTTLE_SEM:
            log.info(f"{current_task} Performing {action.value} for {kwargs}")
            try:
                results = await func(**kwargs)
            except Fault as e:
                if "http.503" in str(e.detail):
                    # try to lower the semaphore count to prevent
                    # future throttling, then retry the SOAP call
                    async with ANTI_THROTTLE_ADJUST_LOCK:  # only one adjust at a time
                        try:
                            await asyncio.wait_for(
                                ANTI_THROTTLE_SEM.acquire(), ANTI_THROTTLE_TIMEOUT
                            )
                            global ANTI_THROTTLE_SEM_TOTAL
                            ANTI_THROTTLE_SEM_TOTAL -= 1
                        except asyncio.TimeoutError:
                            log.critical(
                                f"AXL resource throttling has exceeded built-in timeout of {ANTI_THROTTLE_TIMEOUT:.1f} sec"
                            )
                            raise AXLThrottleTimeout(
                                f"AXL resource throttling has exceeded built-in timeout of {ANTI_THROTTLE_TIMEOUT:.1f} sec"
                            ) from None

                    log.debug(
                        f"{current_task} ANTI_THROTTLE_SEM reduced to {ANTI_THROTTLE_SEM_TOTAL} due to 503 error!"
                    )
                    return await self._generic_soap_call(
                        element, action, children, task_number, **kwargs
                    )
                elif action is APICall.GET:
                    if "was not found" in e.message:
                        log.info(f"{current_task} Completed, but could not find item")
                        return {}
                    else:
                        log.exception(e)
                        raise
                elif action is APICall.LIST:
                    log.info(f"{current_task} Completed, but list empty")
                    return []
                else:
                    log.exception(e)
                    raise

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
        """Same as `_generic_soap_call`, but looks for 'uuid' and the value of 'base_field' in the supplied kwargs.
         If a UUID exists, it will use this value in the SOAP call over the 'base_field' value.

        :param element: SOAP element to be called
        :param action: Type of action to be performed (usually the prefix of `element`)
        :param base_field: The field that will be used if 'uuid' is not found
        :param children: Children of expected response that drill down to the actual data, defaults to None
        :param task_number: Override the logged task number, useful for batch operations, defaults to None
        :param kwargs: Fields added to the SOAP call
        :return: The Zeep object(s), or an empty list/dict depending on the type of `action`
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

    async def _generic_soap_get_many(
        self,
        element: str,
        base_field: str,
        base_list: list[str] = None,
        uuid_list: list[str] = None,
        children: list[str] = None,
        **kwargs,
    ) -> list[dict]:
        """Runs multiple `__generic_soap_with_uuid` requests concurrently with the given `base_field` or `uuid`
         values. The `kwargs` are applied to all SOAP calls.

        :param element: SOAP element to be called
        :param base_field: The field that will be used in the SOAP request for all items in `base_list`
        :param base_list: 'base_field' items used to generate requests, defaults to None
        :param uuid_list: 'uuid' items used to generate requests, defaults to None
        :param children: Children of expected response that drill down to the actual data, defaults to None
        :param kwargs: Fields added to ALL the SOAP requests
        :return: The Zeep objects, or an empty list
        """
        if base_list is not None and uuid_list is None:
            kwargs_list = [
                {base_field: base_value, **kwargs} for base_value in base_list
            ]
        elif uuid_list is not None and base_list is None:
            kwargs_list = [{"uuid": uuid_value, **kwargs} for uuid_value in uuid_list]
        elif all(var is not None for var in (base_list, uuid_list)):
            raise InvalidArguments(
                f"Cannot accept lists for '{base_field}' and 'uuid' at the same time"
            )
        elif all(var is None for var in (base_list, uuid_list)):
            raise InvalidArguments(f"No values supplied for '{base_field}' or 'uuid'")
        else:
            raise DumbProgrammerException(
                "Case not accounted for in conditionals (soap_get_many, choose list)"
            ) from None

        if len(kwargs_list) == 0:
            raise InvalidArguments(f"Empty list supplied for '{base_field}' or 'uuid'")

        task_number = await checkout_task()
        return await asyncio.gather(
            *[
                self._generic_soap_call(
                    element, APICall.GET, children, task_number, **kw
                )
                for kw in kwargs_list
            ]
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

    async def _generic_soap_list(
        self,
        element: str,
        children: list[str] = None,
        task_number: int = None,
        *,
        block_size: int = 1024,
        repeat_tries: int = 0,
        **kwargs,
    ) -> list:
        """Processes a 'list' request in block-sized chunks to prevent AXL from timing out. Performs on-the-fly adjustments to block-sizes when timeouts occur.

        :param element: SOAP element to be called
        :param children: Children of expected response that drill down to the actual data, defaults to None
        :param task_number: Override the logged task number, useful for batch operations, defaults to None
        :param block_size: FOR RECURSIVE USE ONLY, DO NOT USE IN A METHOD CALL
        :param repeat_tries: FOR RECURSIVE USE ONLY, DO NOT USE IN A METHOD CALL
        :return: list of Zeep elements (or empty list if there are no elements found)
        """
        if task_number is not None:
            this_task = task_number
        else:
            this_task = await checkout_task()
        list_results = []

        async def list_worker(index_queue: asyncio.Queue, worker_name: str) -> None:
            while True:
                log.debug(f"{worker_name} is waiting for a queue item...")
                index = await index_queue.get()
                log.debug(f"{worker_name} got {index=}")
                results = await self._generic_soap_call(
                    element,
                    APICall.LIST,
                    children,
                    this_task,
                    first=block_size,
                    skip=index,
                    **kwargs,
                )
                if results:
                    log.debug(
                        f"{worker_name} found {len(results)} results, adding queue task for index={index + (block_size * 3)}"
                    )
                    index_queue.put_nowait(index + (block_size * 3))
                list_results.append(results)
                index_queue.task_done()
                log.debug(
                    f"{worker_name} task done, there are {index_queue.qsize()} tasks currently in the queue"
                )

        q = asyncio.Queue()
        q.put_nowait(0)
        q.put_nowait(block_size)
        q.put_nowait(block_size * 2)

        try:
            worker_tasks = [
                asyncio.create_task(list_worker(q, f"Worker {i}")) for i in range(3)
            ]

            # make sure queue completes before workers do (i.e. from an exception)
            await asyncio.wait(
                [q.join(), *worker_tasks], return_when=asyncio.FIRST_COMPLETED
            )
            for worker in worker_tasks:
                if worker.done():
                    raise worker.exception()
                worker.cancel()
            return list(chain.from_iterable(list_results))
        except httpx.ReadTimeout:
            for worker in worker_tasks:
                worker.cancel()
            if repeat_tries >= 3:
                raise AXLTimeout(element) from None

            log.warning(
                f"{element} request was too large and timed out. Trying reduced chunk size {block_size} -> {int(block_size / 4)} ({3 - repeat_tries} attempts left)"
            )
            return await self._generic_soap_list(
                element,
                children,
                this_task,
                block_size=int(block_size / 4),
                repeat_tries=(repeat_tries + 1),
                **kwargs,
            )

    async def _generic_soap_update(
        self,
        element: str,
        base_field: str,
        base_value: str = None,
        uuid: str = None,
        task_number: int = None,
        **kwargs,
    ) -> bool:
        """Processes an 'update' request for a given `base_field` or `uuid`. Empty strings and None values are parsed out and not sent in SOAP call.

        :param element: SOAP element to be caled
        :param base_field: The field name used to identify the desired object (i.e. for updatePhone, this would be 'name')
        :param base_value: The value for the field described by `base_field`, defaults to None
        :param uuid: The uuid of the desired object, defaults to None
        :param task_number: Override the logged task number, useful for batch operations, defaults to None
        :return: True if update succeeded, False otherwise
        """
        if uuid:
            kw_group = {base_field: base_value}
        elif base_value:
            kw_group = {"uuid": uuid}
        else:
            raise InvalidArguments(f"A '{base_field}' or 'uuid' value must be given")

        kw_group.update(
            {k: v for k, v in kwargs.items() if (v != "" and v is not None)}
        )

        try:
            result = await self._generic_soap_call(
                element, APICall.UPDATE, None, task_number, **kw_group
            )
        except Fault as e:
            raise AXLFault(e) from None

        if not result:
            return False
        else:
            return True

    async def _gather_method_calls(
        self, method: str, kwargs_list: list[dict]
    ) -> list[dict]:
        """Helper method for concurrently running multiple method calls

        :param method: AsyncAXL method, by name
        :param kwargs_list: list of kw arguments to be sent with each method call
        :return: list of results in dict form (assuming @serialize is used on the given method)
        """
        if (func := getattr(self, method, None)) is None:
            raise DumbProgrammerException(f"{method} is not an AsyncAXL method")

        log.debug(f"Starting {len(kwargs_list)} {method} tasks...")
        results = await asyncio.gather(*[func(**kw) for kw in kwargs_list])
        log.debug(
            f"Finished {len(kwargs_list)} {method} tasks, returned {len([x for x in results if x])} items"
        )
        return results

    async def _check_exists(
        self,
        element: str,
        field: Union[str, list[str]],
        field_value: Union[str, list[str]],
        task_number: int = None,
        **kwargs,
    ) -> bool:
        """Performs a call to the given `element` (usually a 'get' element) to determine if an object exists.

        :param element: SOAP element to be called
        :param field: The name or names of the field(s) needed to search for the object (i.e. for getLine this would be ['pattern', 'routePartitionName'])
        :param field_value: The value or values of the provided field(s)
        :param task_number: Override the logged task number, useful for batch operations, defaults to None
        :return: True if the object was found, False if it wasn't
        """
        if type(field) == str:
            kw_group = {field: field_value}
        elif isinstance(type(field), Sequence):
            kw_group = {f[0]: f[1] for f in zip(field, field_value)}
        else:
            raise DumbProgrammerException("Bad types for field or field_value")

        # try:
        results = await self._generic_soap_call(
            element,
            APICall.GET,
            task_number=task_number,
            **kw_group,
            returnedTags={},
            **kwargs,
        )
        # except Fault:
        #     return {False: field_value}

        if not results:
            return False
        else:
            return True

    async def _check_exists_many(
        self,
        element: str,
        field: Union[str, list[str]],
        field_values: Union[list[str], list[list[str]]],
        task_number: int = None,
        **kwargs,
    ) -> CheckResults:
        """Performs multiple calls to the given 'element' to determine if multiple objects exist

        :param element: SOAP element to be called
        :param field: The name or names of the field(s) needed to search for the object (i.e. for getLine this would be ['pattern', 'routePartitionName'])
        :param field_values: A list of values (or a nested list of values if multiple fields are required) corresponding to the provided field(s)
        :param task_number: Override the logged task number, useful for batch operations, defaults to None
        :return: A named-tuple with the first value 'found' containing a list of `field_values` that were found, and the second value 'missing' with the rest that were not found
        """
        check_tasks = {
            asyncio.create_task(
                self._check_exists(element, field, fv, task_number, **kwargs)
            ): fv
            for fv in field_values
        }
        await asyncio.wait(check_tasks)

        result = CheckResults([], [])

        for task in check_tasks:
            if task.exception() is not None or not task.result():
                result.missing.append(check_tasks[task])
            else:
                result.found.append(check_tasks[task])

        return result

    def __extract_template(self, element_name: str, template: dict, child="") -> dict:
        """Removes all unnecessary values from a device/line/etc template, like None and "" values. Keeps any values that are required by the given `element_name`, regardless of what the values are.

        :param element_name: The 'get' element originally used to get the template
        :param template: The template data, used to create a "blank" template
        :param child: Name of element's child if data in `template` is a child of the given `element_name`. NOTE: this is DIFFERENT from `children` seen in other helper methods.
        :return: Template data removed of unnecessary values
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

        :param template_name: The name of a phone template in Bulk Administration -> Phones -> Phone Template
        :return: The parsed template data
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

        :param template_name: The name of a line template from one of the phone templates in Bulk Administration -> Phones -> Phone Template
        :param template_route_partition: The route partition of the template line
        :return: The parsed template data
        """
        template_data = await self.get_directory_number(
            pattern=template_name,
            route_partition=template_route_partition,
        )
        template_data.update({"active": "true", "usage": Nil}, **kwargs)
        return self.__extract_template("addLine", template_data, "line")

    ####################
    # ==== PHONES ==== #
    ####################

    @serialize
    @check_tags("getPhone")
    async def get_phone(
        self, name="", uuid="", *, return_tags: list[str] = None
    ) -> dict:
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
        return await self._generic_soap_get_many(
            "getPhone",
            "name",
            names,
            uuids,
            ["return", "phone"],
            returnedTags=return_tags,
        )

    @serialize
    @check_tags("listPhone")
    async def find_phones(
        self,
        name_search="%",
        desc_search="%",
        css_search="%",
        pool_search="%",
        security_profile_search="%",
        *,
        return_tags: list[str] = None,
    ) -> list[dict]:
        query = {
            "name": name_search,
            "description": desc_search,
            "callingSearchSpaceName": css_search,
            "devicePoolName": pool_search,
            "securityProfileName": security_profile_search,
        }
        query = {k: v for k, v in query.items() if v and v != "%"}
        if len(query) == 0:
            raise InvalidArguments("No search query supplied")

        return await self._generic_soap_list(
            "listPhone",
            ["return", "phone"],
            searchCriteria=query,
            returnedTags=return_tags,
        )

    @serialize
    @check_tags("listPhone")
    async def list_phones(self, *, return_tags: list[str] = None) -> list[dict]:
        return await self._generic_soap_list(
            "listPhone",
            ["return", "phone"],
            searchCriteria={"name": "%"},
            returnedTags=return_tags,
        )

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

    @check_arguments("updatePhone")
    async def update_phone(
        self,
        name: str,
        new_name="",
        description="",
        css="",
        device_pool="",
        button_template="",
        common_phone_profile="",
        location="",
        use_trusted_relay_point: Union[str, bool] = "",
        built_in_bridge="",
        packet_capture_mode="",
        mobility_mode="",
        **kwargs,
    ) -> None:
        pass  # TODO: figure out most needed update arguments

    #########################
    # ==== PHONE LINES ==== #
    #########################

    @serialize
    async def get_phone_lines(self, name="", uuid="") -> list[dict]:
        tags = fix_return_tags(self.zeep, "getPhone", ["lines"])
        result = await self._generic_soap_with_uuid(
            "getPhone",
            APICall.GET,
            "name",
            ["return", "phone", "lines", "line"],
            name=name,
            uuid=uuid,
            returnedTags=tags,
        )
        if not result:
            return []  # adjust for empty dict returned
        else:
            return result

    async def add_phone_line(
        self,
        pattern: str,
        route_partition: str,
        phone_name="",
        phone_uuid="",
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

        lock = PHONE_MANIP_LOCKS[phone_name]
        if lock.locked():
            log.debug(f"Waiting on locked resource for phone '{phone_name}'")

        async with lock:
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

    ###############################
    # ==== DIRECTORY NUMBERS ==== #
    ###############################

    @serialize
    @check_tags("getLine")
    async def get_directory_number(
        self,
        dn: tuple[str, str] = None,
        uuid="",
        *,
        return_tags: list[str] = None,
    ) -> dict:
        kw_group = {}
        if dn is None and uuid:
            kw_group["uuid"] = uuid
        elif type(dn) == tuple and len(dn) == 2:
            kw_group["pattern"] = dn[0]
            kw_group["routePartitionName"] = dn[1]
        else:
            raise InvalidArguments(
                "get_directory_number() must either receive a 'dn' tuple of (pattern, route partition) or a 'uuid'. "
                + "If you don't know the route partition of the DN you are looking for, try using find_directory_numbers()"
            )

        return await self._generic_soap_call(
            "getLine",
            APICall.GET,
            ["return", "line"],
            **kw_group,
            returnedTags=return_tags,
        )

    @serialize
    @check_tags("getLine")
    async def get_directory_numbers(
        self,
        dn_list: Sequence[tuple[str, str]] = None,
        uuids: list[str] = None,
        *,
        return_tags: list[str] = None,
    ) -> list[dict]:
        coro_list = []
        if dn_list is not None:
            try:
                coro_list += [
                    self.get_directory_number(dn=dn, return_tags=return_tags)
                    for dn in dn_list
                ]
            except TypeError:
                raise InvalidArguments(
                    "'dn_list' needs to be a sequence of tuples"
                ) from None
        if uuids is not None:
            try:
                coro_list += [
                    self.get_directory_number(uuid=uuid, return_tags=return_tags)
                    for uuid in uuids
                ]
            except TypeError:
                raise InvalidArguments("'uuids' needs to be a list") from None

        return await asyncio.gather(*coro_list)

    @serialize
    @check_tags("listLine")
    async def find_directory_numbers(
        self,
        pattern_search="",
        route_partition_search="",
        desc_search="",
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
        # args_pack["returnedTags"] = return_tags

        return await self._generic_soap_call(
            "listLine",
            APICall.LIST,
            ["return", "line"],
            searchCriteria=args_pack,
            returnedTags=return_tags,
        )

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

    #############################
    # ==== DEVICE PROFILES ==== #
    #############################

    @serialize
    @check_tags("getDeviceProfile")
    async def get_device_profile(
        self, name="", uuid="", *, return_tags: list[str] = None
    ) -> dict:
        return await self._generic_soap_with_uuid(
            "getDeviceProfile",
            APICall.GET,
            "name",
            ["return", "deviceProfile"],
            uuid=uuid,
            name=name,
            returnedTags=return_tags,
        )

    @check_arguments("addDeviceProfile")
    async def add_device_profile(
        self,
        name: str,
        button_template: str,
        description="",
        model="Cisco 8845",
        protocol="SIP",
        services: list[dict] = None,
    ) -> None:
        pass  # TODO: continue writing method

    ###################
    # ==== USERS ==== #
    ###################

    @serialize
    @check_tags("getUser")
    async def get_user(
        self, user_id="", uuid="", *, return_tags: list[str] = None
    ) -> dict:
        return await self._generic_soap_with_uuid(
            "getUser",
            APICall.GET,
            "userid",
            ["return", "user"],
            task_number=await checkout_task(),
            userid=user_id,
            uuid=uuid,
            returnedTags=return_tags,
        )

    @serialize
    @check_tags("getUser")
    async def get_users(
        self,
        user_ids: list[str] = None,
        uuids: list[str] = None,
        *,
        return_tags: list[str] = None,
    ) -> list[dict]:
        return await self._generic_soap_get_many(
            "getUser",
            "userid",
            user_ids,
            uuids,
            ["return", "user"],
            returnedTags=return_tags,
        )

    @check_arguments("updateUser")
    async def update_user(
        self,
        user_id="",
        uuid="",
        new_user_id="",
        name: tuple[str, str, str] = None,
        telephone_number="",
        mobile_number="",
        home_number="",
        title="",
        password="",
        pin="",
        department="",
        manager="",
        primary_ext: tuple[str, str] = None,
        default_profile="",
        subscribe_css="",
        enable_cti: bool = None,
        enable_mobility: bool = None,
        enable_mobile_voice: bool = None,
        enable_emcc: bool = None,
        enable_home_cluster: bool = None,
        enable_im_presence: bool = None,
        enable_meeting_presence: bool = None,
        enable_host_conf_now: bool = None,
        **kwargs,
    ) -> bool:
        kw_map = {
            "newUserid": new_user_id,
            "telephoneNumber": telephone_number,
            "mobileNumber": mobile_number,
            "homeNumber": home_number,
            "title": title,
            "password": password,
            "pin": pin,
            "department": department,
            "manager": manager,
            "defaultProfile": default_profile,
            "subscribeCallingSearchSpaceName": subscribe_css,
            "enableCti": enable_cti,
            "enableMobility": enable_mobility,
            "enableMobileVoiceAccess": enable_mobile_voice,
            "enableEmcc": enable_emcc,
            "homeCluster": enable_home_cluster,
            "imAndPresenceEnable": enable_im_presence,
            "calendarPresence": enable_meeting_presence,
            "enableUserToHostConferenceNow": enable_host_conf_now,
            **kwargs,
        }
        if name is not None:
            kw_map.update(
                {
                    "firstName": name[0],
                    "middleName": name[1],
                    "lastName": name[2],
                }
            )
        if primary_ext is not None:
            kw_map.update(
                {
                    "primaryExtension": {
                        "pattern": primary_ext[0],
                        "routePartitionName": primary_ext[1],
                    },
                }
            )

        return await self._generic_soap_update(
            "updateUser", "userid", user_id, uuid, **kw_map
        )

    async def add_user_associated_device(
        self,
        user_id="",
        uuid="",
        device: Union[str, Sequence] = None,
    ) -> None:
        if user_id:
            query = {"userid": user_id}
        elif uuid:
            query = {"uuid": uuid}
        else:
            raise InvalidArguments("A 'user_id' or 'uuid' must be provided")

        # in the background, check if device(s) exists
        if type(device) == str:
            check_device = asyncio.create_task(
                self._check_exists("getPhone", "name", device)
            )
            new_devices = [device]
        elif isinstance(type(device), Sequence):
            check_device = asyncio.create_task(
                self._check_exists_many("getPhone", "name", device)
            )
            new_devices = [d for d in device]
        else:
            raise InvalidArguments(
                f"'device' must be a str or sequence (list, tuple, etc), not {type(device)}"
            )

        # make sure current user exists
        user_result = await self.get_user(**query, return_tags=["userid"])
        if not user_result:
            raise InvalidArguments(f"{query} could not be found")

        this_user_id = user_result["userid"]

        await check_device
        if (exc := check_device.exception()) is not None:
            raise exc
        if type(dev_result := check_device.result()) == bool and not dev_result:
            raise InvalidArguments(f"Could not find device '{device}'")
        elif isinstance(dev_result, CheckResults) and dev_result.missing:
            raise InvalidArguments(f"Could not find device(s) '{dev_result.missing}'")

        # prevent user from being edited during update
        async with USER_MANIP_LOCKS[this_user_id]:
            user_devices = await self.get_user(
                this_user_id, return_tags=["associatedDevices"]
            )["associatedDevices"]

            if user_devices is None:
                current_devices = []
            else:
                current_devices = user_devices["device"]

            update_devices = new_devices + current_devices

            await self._generic_soap_call(
                "updateUser",
                APICall.UPDATE,
                userid=this_user_id,
                associatedDevices={"device": update_devices},
            )

    async def add_user_cti_profile(
        self,
        user_id="",
        uuid="",
        profile: Union[str, list[str]] = None,
    ) -> None:
        pass  # TODO: continue writing method


async def checkout_task() -> int:
    """Retrieves a task number for a single task"""
    global TASK_COUNT_LOCK
    global TASK_COUNTER

    lock = TASK_COUNT_LOCK
    async with lock:
        TASK_COUNTER += 1
        return copy(TASK_COUNTER)


def task_string(task_number: int) -> str:
    """Turns a given `task_number` into a zero-padded, formatted string"""
    return f"[{str(task_number).zfill(4)}]"


async def checkout_task_str() -> str:
    """Retrieves a task number for a single task and returns a formatted task string"""
    task_number = await checkout_task()
    return task_string(task_number)
