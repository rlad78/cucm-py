"""
Class to interface with cisco ucm axl api.
Author: Jeff Levensailor
Version: 0.1
Dependencies:
 - zeep: https://python-zeep.readthedocs.io/en/master/

Links:
 - https://developer.cisco.com/site/axl/
"""

from typing import Callable, TypeVar, Union, overload
from typing_extensions import ParamSpec
from cucmtoolkit.ciscoaxl.validation import (
    validate_ucm_server,
    validate_axl_auth,
    get_ucm_version,
)
from cucmtoolkit.ciscoaxl.exceptions import *
from cucmtoolkit.ciscoaxl.wsdl import (
    get_return_tags,
    fix_return_tags,
    print_element_layout,
)
import cucmtoolkit.ciscoaxl.configs as cfg
import re
import urllib3
from pathlib import Path
from requests import Session
from requests.auth import HTTPBasicAuth
from zeep import Client, Settings
from zeep.transports import Transport
from zeep.cache import SqliteCache
from zeep.exceptions import Fault
from zeep.helpers import serialize_object
from functools import wraps, singledispatchmethod
import inspect

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# For use with decorators listed below. Helps supply correct params in Pylance, Intellisense, etc.
# Reference: https://github.com/microsoft/pyright/issues/774#issuecomment-755769085
# _sP = ParamSpec("_sP")
# _sR = TypeVar("_sR")

# _slP = ParamSpec("_slP")
# _slR = TypeVar("_slR")

# _ctP = ParamSpec("_ctP")
# _ctR = TypeVar("_ctR")

TCallable = TypeVar("TCallable", bound=Callable)


def serialize(func: TCallable) -> TCallable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        r_value = func(*args, **kwargs)
        if cfg.DISABLE_SERIALIZER:
            return r_value

        if r_value is None:
            return dict()
        elif issubclass(type(r_value), Exception):
            return r_value
        elif (
            "return_tags" not in kwargs
            and (
                tags_param := inspect.signature(func).parameters.get(
                    "return_tags", None
                )
            )
            is not None
        ):
            r_dict = serialize_object(r_value, dict)
            return _tag_serialize_filter(tags_param.default, r_dict)
        elif "return_tags" in kwargs:
            r_dict = serialize_object(r_value, dict)
            return _tag_serialize_filter(kwargs["return_tags"], r_dict)
        else:
            return serialize_object(r_value, dict)

    return wrapper


def serialize_list(func: TCallable) -> TCallable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        r_value = func(*args, **kwargs)
        if cfg.DISABLE_SERIALIZER:
            return r_value

        if type(r_value) != list:
            return r_value
        elif (
            "return_tags" not in kwargs
            and (
                tags_param := inspect.signature(func).parameters.get(
                    "return_tags", None
                )
            )
            is not None
        ):
            return [
                _tag_serialize_filter(
                    tags_param.default, serialize_object(element, dict)
                )
                for element in r_value
            ]
        elif "return_tags" in kwargs:
            return [
                _tag_serialize_filter(
                    kwargs["return_tags"], serialize_object(element, dict)
                )
                for element in r_value
            ]

    return wrapper


def check_tags(element_name: str):
    def check_tags_decorator(func: TCallable) -> TCallable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if cfg.DISABLE_CHECK_TAGS:
                return func(*args, **kwargs)

            if type(args[0]) != axl:
                raise DumbProgrammerException(
                    f"Forgot to include self in {func.__name__}!!!!"
                )
            elif (
                tags_param := inspect.signature(func).parameters.get(
                    "return_tags", None
                )
            ) is None:
                raise DumbProgrammerException(
                    f"No 'return_tags' param on {func.__name__}()"
                )
            elif tags_param.kind != tags_param.KEYWORD_ONLY:
                raise DumbProgrammerException(
                    f"Forgot to add '*' before return_tags on {func.__name__}()"
                )
            elif not element_name:
                raise DumbProgrammerException(
                    f"Forgot to provide element_name in check_tags decorator on {func.__name__}!!!"
                )
            elif "return_tags" not in kwargs:
                # tags are default
                return func(*args, **kwargs)
            elif type(kwargs["return_tags"]) == list:
                # supply all legal tags if an empty list is provided
                if len(kwargs["return_tags"]) == 0:
                    kwargs["return_tags"] = fix_return_tags(
                        z_client=args[0].zeep,
                        element_name=element_name,
                        tags=get_return_tags(args[0].zeep, element_name),
                    )
                    return func(*args, **kwargs)
                else:
                    kwargs["return_tags"] = fix_return_tags(
                        z_client=args[0].zeep,
                        element_name=element_name,
                        tags=kwargs["return_tags"],
                    )
                    return func(*args, **kwargs)

        wrapper.element_name = element_name
        return wrapper

    return check_tags_decorator


def operation_tag(element_name: str):
    def operation_tag_decorator(func: TCallable) -> TCallable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        wrapper.element_name = element_name
        return wrapper

    return operation_tag_decorator


class axl(object):
    """
    The AXL class sets up the connection to the call manager with methods for configuring UCM.
    Tested with environment of;
    Python 3.6
    """

    def __init__(
        self, username: str, password: str, cucm: str, port="8443", version=""
    ):
        """Main object for interfacing with the AXL API

        Parameters
        ----------
        username : str
            Admin with AXL privileges
        password : str
            Password for admin user
        cucm : str
            Base URL where a UCM server is located
        port : str, optional
            The port at which UCM services can be accessed, by default "8443"
        version : str, optional
            Only required if getting UDS exceptions, by default "". Use a two-digit version, like "11.5" or "14.0"

        Raises
        ------
        UCMVersionInvalid
            if an invalid UCM version is provided
        UCMException
            if an issue regarding UCM is found
        AXLException
            if an issue regarding the AXL API is found
        """
        try:
            ucm_validation = validate_ucm_server(cucm)
        except (UCMInvalidError, UCMConnectionFailure, UCMNotFoundError) as err:
            raise UCMException(err)
        if not ucm_validation:
            raise UCMException(
                f"Could not connect to {cucm}, please check your server."
            )

        if version != "":
            cucm_version = version
        else:
            cucm_version = get_ucm_version(cucm, port)

        wsdl_path = cfg.ROOT_DIR / "schema" / cucm_version / "AXLAPI.wsdl"
        if not wsdl_path.parent.is_dir():
            raise UCMVersionInvalid(cucm_version)
        wsdl = str(wsdl_path)

        session = Session()
        session.verify = False
        session.auth = HTTPBasicAuth(username, password)
        settings = Settings(
            strict=False, xml_huge_tree=True, xsd_ignore_sequence_order=True
        )
        transport = Transport(session=session, timeout=10, cache=SqliteCache())
        axl_client = Client(wsdl, settings=settings, transport=transport)

        self.username = username
        self.password = password
        self.zeep = axl_client
        self.wsdl = wsdl
        self.cucm = cucm
        self.cucm_port = port
        self.cucm_version = cucm_version

        try:
            axl_validation = validate_axl_auth(cucm, username, password, port)
        except (AXLInvalidCredentials, AXLConnectionFailure, AXLNotFoundError) as err:
            raise AXLException(err)
        if not axl_validation:
            raise AXLException()

        self.UUID_PATTERN = re.compile(
            r"^[\da-f]{8}-([\da-f]{4}-){3}[\da-f]{12}$", re.IGNORECASE
        )
        self.client = axl_client.create_service(
            "{http://www.cisco.com/AXLAPIService/}AXLAPIBinding",
            f"https://{cucm}:{port}/axl/",
        )

    def print_axl_arguments(self, method_name: str) -> None:
        """Prints the full argument list for the AXL request associated with a method. Use this on methods that require you to know category names for things like update operations and queries.

        Parameters
        ----------
        method_name : str
            The name of the method you wish to investigate. Do not include parenthesis () or prefix with 'axl.'

        Raises
        ------
        Exception
            when the method name provided isn't valid, or there isn't an associated AXL request (no XSD element)
        """
        method = getattr(self, method_name, None)
        if method is None:
            raise Exception(f"'{method_name}' is not a valid method of the 'axl' class")

        if not hasattr(method, "element_name"):
            raise Exception(f"'{method_name}' does not have an associated XSD element")

        print_element_layout(self.zeep, method.element_name)

    @serialize_list
    @check_tags(element_name="listLocation")
    def get_locations(
        self,
        name="%",
        *,
        return_tags=[
            "name",
            "withinAudioBandwidth",
            "withinVideoBandwidth",
            "withinImmersiveKbits",
        ],
    ) -> Union[list[dict], None]:
        """Get all locations created in UCM

        Parameters
        ----------
        name : str, optional
            Name to search against all locations, by default "%", the SQL "any" wildcard.
        return_tags : list, optional, keyword-only
            The categories to be returned, by default [ "name", "withinAudioBandwidth", "withinVideoBandwidth", "withinImmersiveKbits", ]. If an empty list is provided, all categories will be returned.

        Returns
        -------
        list[dict]
            A list of all location info.
        Fault
            The error returned from AXL, if one occured.
        """
        if return_tags and type(return_tags[0]) == dict:
            tags = return_tags[0]
        elif return_tags:
            tags = {t: "" for t in return_tags}

        try:
            return self.client.listLocation({"name": name}, returnedTags=tags,)[
                "return"
            ]["location"]
        except Fault as e:
            return e

    def run_sql_query(self, query: str) -> dict:
        """Legacy function. Use sql_query() instead.

        Parameters
        ----------
        query : str
            SQL query to be run.

        Returns
        -------
        dict
            Contains 'num_rows', 'query', and 'rows' only if the query returned anything. Otherwise, only returns 'num_rows' = 0 and 'query' = query.
        Fault
            The error returned from AXL, if one occured.
        """
        result = {"num_rows": 0, "query": query}

        try:
            sql_result = self.sql_query(query)
        except Exception as fault:
            sql_result = None
            self.last_exception = fault

        num_rows = 0
        result_rows = []

        if sql_result is not None:
            for row in sql_result["row"]:
                result_rows.append({})
                for column in row:
                    result_rows[num_rows][column.tag] = column.text
                num_rows += 1

        result["num_rows"] = num_rows
        if num_rows > 0:
            result["rows"] = result_rows

        return result

    def sql_query(self, query: str) -> Union[list[list[str]], Fault]:
        """Runs an SQL query on the UCM DB and returns the results.

        Parameters
        ----------
        query : str
            The SQL query to run. Do not include "run sql" in your query (as you would in the UCM CLI interface)

        Returns
        -------
        list[list[str]]
            The returned SQL rows in the form of a nested list, with the first "row" being the headers.
        Fault
            The error returned from AXL, if one occured.
        """
        try:
            recv = self.client.executeSQLQuery(query)["return"]
            data = recv["row"]
        except Fault as e:
            return e
        except (KeyError, TypeError):  # no rows returned
            return [[]]
        if not data:  # data is empty
            return [[]]

        # Zeep returns nested list of Element objs
        # Need to extract text from all Element objs
        parsed_data: list[list[str]] = []
        parsed_data.append([e.tag for e in data[0]])  # headers
        for row in data:
            parsed_data.append([e.text for e in row])

        return parsed_data

    def sql_update(self, query: str) -> Union[dict, Fault]:
        """Run an update on the UCM SQL DB.

        Parameters
        ----------
        query : str
            The SQL query to run. Do not include "run sql" in your query (as you would in the UCM CLI interface)

        Returns
        -------
        dict
            The response from AXL if all goes well
        Fault
            The error returned from AXL, if one occured
        """
        try:
            return self.client.executeSQLUpdate(query)["return"]
        except Fault as e:
            return e

    @serialize_list
    @check_tags("listLdapDirectory")
    def get_ldap_dir(
        self,
        *,
        return_tags=[
            "name",
            "ldapDn",
            "userSearchBase",
        ],
    ) -> Union[dict, None]:
        """Get LDAP syncs

        Parameters
        ----------
        return_tags : list, optional, keyword-only
            The categories to be returned, by default [ "name", "ldapDn", "userSearchBase", ]. If an empty list is provided, all categories will be returned.

        Returns
        -------
        dict
            The response from AXL if all goes well
        Fault
            The error returned from AXL, if one occured
        """
        try:
            return self.client.listLdapDirectory(
                {"name": "%"},
                returnedTags={k: "" for k in return_tags},
            )["return"]["ldapDirectory"]
        except Fault as e:
            return e

    # ? don't want to do LDAP sync to test this one out...
    @serialize
    def do_ldap_sync(self, uuid):
        """
        Do LDAP Sync
        :param uuid: uuid
        :return: result dictionary
        """
        try:
            return self.client.doLdapSync(uuid=uuid, sync=True)
        except Fault as e:
            return e

    @serialize
    @operation_tag("doChangeDNDStatus")
    def do_change_dnd_status(
        self, user_id: str, dnd_enabled: bool
    ) -> Union[dict, Fault]:
        """Turns on/off DND for all devices associated with a given user.

        Parameters
        ----------
        user_id : str
            The user whose devices you want to change DND status
        dnd_enabled : bool
            True to turn on DND, False to turn it off

        Returns
        -------
        dict
            The response from AXL if all goes well
        Fault
            The error returned from AXL, if one occured
        """
        try:
            return self.client.doChangeDNDStatus(userID=user_id, dndStatus=dnd_enabled)
        except Fault as e:
            return e

    # ? no idea what this does
    @operation_tag("doDeviceLogin")
    def do_device_login(self, **args):
        """
        Do Device Login
        :param deviceName:
        :param userId:
        :param profileName:
        :return: result dictionary
        """
        try:
            return self.client.doDeviceLogin(**args)
        except Fault as e:
            return e

    # ? no idea what this does
    @operation_tag("doDeviceLogout")
    def do_device_logout(self, **args):
        """
        Do Device Logout
        :param device:
        :param userId:
        :return: result dictionary
        """
        try:
            return self.client.doDeviceLogout(**args)
        except Fault as e:
            return e

    @serialize
    @operation_tag("doDeviceReset")
    def do_device_reset(self, name="", uuid="") -> Union[dict, Fault, None]:
        """Sends a device reset to the requested phone. Same as pressing the "Reset" button on a phone in the UCM web interface.

        Parameters
        ----------
        name : str, optional
            The device name. If uuid is also provided, this value will be ignored.
        uuid : str, optional
            The uuid of the device. If provided, the name value will be ignored.

        Returns
        -------
        dict
            The response from AXL if all goes well.
        Fault
            The error returned from AXL, if one occurs.
        None
            If neither name nor uuid are supplied as parameters (no action taken).
        """
        if name != "" and uuid == "":
            try:
                return self.client.doDeviceReset(deviceName=name, isHardReset=True)
            except Fault as e:
                return e
        elif uuid != "":
            try:
                return self.client.doDeviceReset(uuid=uuid, isHardReset=True)
            except Fault as e:
                return e

    # ? can't risk testing this
    @operation_tag("resetSipTrunk")
    def reset_sip_trunk(self, name="", uuid=""):
        """
        Reset SIP Trunk
        :param name: device name
        :param uuid: device uuid
        :return: result dictionary
        """
        if name != "" and uuid == "":
            try:
                return self.client.resetSipTrunk(name=name)
            except Fault as e:
                return e
        elif name == "" and uuid != "":
            try:
                return self.client.resetSipTrunk(uuid=uuid)
            except Fault as e:
                return e

    @serialize
    @operation_tag("getLocation")
    def get_location(self, name="", uuid="") -> Union[dict, Fault, None]:
        """Finds the requested location and returns data on that location.

        Parameters
        ----------
        name : str, optional
            Name of the location. If uuid is also provided, this value will be ignored.
        uuid : str, optional
            The uuid of the location. If provided, the name value will be ignored.

        Returns
        -------
        dict
            The information on the requested location.
        Fault
            The error returned from AXL, if one occurs.
        None
            If neither name nor uuid are supplied as parameters (no action taken).
        """
        if name != "" and uuid == "":
            try:
                return self.client.getLocation(name=name)
            except Fault as e:
                return e
        elif uuid != "":
            try:
                return self.client.getLocation(uuid=uuid)
            except Fault as e:
                return e
        else:
            return None

    # ! I'm definitely gonna need help with this one...
    def add_location(
        self,
        name: str,
        kbits=512,
        video_kbits=-1,
        within_audio_bw=512,
        within_video_bw=-1,
        within_immersive_kbits=-1,
    ):
        """
        Add a location
        :param name: Name of the location to add
        :param cucm_version: ucm version
        :param kbits: ucm 8.5
        :param video_kbits: ucm 8.5
        :param within_audio_bw: ucm 10
        :param within_video_bw: ucm 10
        :param within_immersive_kbits: ucm 10
        :return: result dictionary
        """
        if (
            self.cucm_version == "8.6"
            or self.cucm_version == "9.0"
            or self.cucm_version == "9.5"
            or self.cucm_version == "10.0"
        ):
            try:
                return self.client.addLocation(
                    {
                        "name": name,
                        # CUCM 8.6
                        "kbits": kbits,
                        "videoKbits": video_kbits,
                    }
                )
            except Fault as e:
                return e
        else:
            try:
                betweenLocations = []
                betweenLocation = {}
                RLocationBetween = {}
                RLocationBetween["locationName"] = "Hub_None"
                RLocationBetween["weight"] = 0
                RLocationBetween["audioBandwidth"] = within_audio_bw
                RLocationBetween["videoBandwidth"] = within_video_bw
                RLocationBetween["immersiveBandwidth"] = within_immersive_kbits
                betweenLocation["betweenLocation"] = RLocationBetween
                betweenLocations.append(betweenLocation)

                return self.client.addLocation(
                    {
                        "name": name,
                        # CUCM 10.6
                        "withinAudioBandwidth": within_audio_bw,
                        "withinVideoBandwidth": within_video_bw,
                        "withinImmersiveKbits": within_immersive_kbits,
                        "betweenLocations": betweenLocations,
                    }
                )
            except Fault as e:
                return e

    @operation_tag("removeLocation")
    def delete_location(self, name="", uuid=""):
        """Deletes the requested location.

        Parameters
        ----------
        name : str, optional
            Name of the location. If uuid is also provided, this value will be ignored.
        uuid : str, optional
            The uuid of the location. If provided, the name value will be ignored.

        Returns
        -------
        dict
            The completion information from AXL.
        Fault
            The error returned from AXL, if one occurs.
        None
            If neither name nor uuid are supplied as parameters (no action taken).
        """
        if name != "" and uuid == "":
            try:
                return self.client.removeLocation(name=name)
            except Fault as e:
                return e
        elif uuid != "":
            try:
                return self.client.removeLocation(uuid=uuid)
            except Fault as e:
                return e
        else:
            return None

    # ! gonna need help with this one too
    def update_location(self, **args):
        """
        Update a Location
        :param name:
        :param uuid:
        :param newName:
        :param withinAudioBandwidth:
        :param withinVideoBandwidth:
        :param withImmersiveKbits:
        :param betweenLocations:
        :return:
        """
        try:
            return self.client.updateLocation(**args)
        except Fault as e:
            return e

    def get_regions(self, tagfilter={"uuid": "", "name": ""}):
        """
        Get region details
        :param mini: return a list of tuples of region details
        :return: A list of dictionary's
        """
        try:
            return self.client.listRegion({"name": "%"}, returnedTags=tagfilter)[
                "return"
            ]["region"]
        except Fault as e:
            return e

    def get_region(self, **args):
        """
        Get region information
        :param name: Region name
        :return: result dictionary
        """
        try:
            return self.client.getRegion(**args)
        except Fault as e:
            return e

    def add_region(self, name):
        """
        Add a region
        :param name: Name of the region to add
        :return: result dictionary
        """
        try:
            return self.client.addRegion({"name": name})
        except Fault as e:
            return e

    def update_region(self, name="", newName="", moh_region=""):
        """
        Update region and assign region to all other regions
        :param name:
        :param uuid:
        :param moh_region:
        :return:
        """
        # Get all Regions
        all_regions = self.client.listRegion({"name": "%"}, returnedTags={"name": ""})
        # Make list of region names
        region_names = [str(i["name"]) for i in all_regions["return"]["region"]]
        # Build list of dictionaries to add to region api call
        region_list = []

        for i in region_names:
            # Highest codec within a region
            if i == name:
                region_list.append(
                    {
                        "regionName": i,
                        "bandwidth": "256 kbps",
                        "videoBandwidth": "-1",
                        "immersiveVideoBandwidth": "-1",
                        "lossyNetwork": "Use System Default",
                    }
                )

            # Music on hold region name
            elif i == moh_region:
                region_list.append(
                    {
                        "regionName": i,
                        "bandwidth": "64 kbps",
                        "videoBandwidth": "-1",
                        "immersiveVideoBandwidth": "-1",
                        "lossyNetwork": "Use System Default",
                    }
                )

            # All else G.711
            else:
                region_list.append(
                    {
                        "regionName": i,
                        "bandwidth": "64 kbps",
                        "videoBandwidth": "-1",
                        "immersiveVideoBandwidth": "-1",
                        "lossyNetwork": "Use System Default",
                    }
                )
        try:
            return self.client.updateRegion(
                name=name,
                newName=newName,
                relatedRegions={"relatedRegion": region_list},
            )
        except Fault as e:
            return e

    def delete_region(self, **args):
        """
        Delete a location
        :param name: The name of the region to delete
        :param uuid: The uuid of the region to delete
        :return: result dictionary
        """
        try:
            return self.client.removeRegion(**args)
        except Fault as e:
            return e

    def get_srsts(self, tagfilter={"uuid": ""}):
        """
        Get all SRST details
        :param mini: return a list of tuples of SRST details
        :return: A list of dictionary's
        """
        try:
            return self.client.listSrst({"name": "%"}, returnedTags=tagfilter)[
                "return"
            ]["srst"]
        except Fault as e:
            return e

    def get_srst(self, name):
        """
        Get SRST information
        :param name: SRST name
        :return: result dictionary
        """
        try:
            return self.client.getSrst(name=name)
        except Fault as e:
            return e

    def add_srst(self, name, ip_address, port=2000, sip_port=5060):
        """
        Add SRST
        :param name: SRST name
        :param ip_address: SRST ip address
        :param port: SRST port
        :param sip_port: SIP port
        :return: result dictionary
        """
        try:
            return self.client.addSrst(
                {
                    "name": name,
                    "port": port,
                    "ipAddress": ip_address,
                    "SipPort": sip_port,
                }
            )
        except Fault as e:
            return e

    def delete_srst(self, name):
        """
        Delete a SRST
        :param name: The name of the SRST to delete
        :return: result dictionary
        """
        try:
            return self.client.removeSrst(name=name)
        except Fault as e:
            return e

    def update_srst(self, name, newName=""):
        """
        Update a SRST
        :param srst: The name of the SRST to update
        :param newName: The new name of the SRST
        :return: result dictionary
        """
        try:
            return self.client.updateSrst(name=name, newName=newName)
        except Fault as e:
            return e

    def get_device_pools(
        self,
        tagfilter={
            "name": "",
            "dateTimeSettingName": "",
            "callManagerGroupName": "",
            "mediaResourceListName": "",
            "regionName": "",
            "srstName": "",
            # 'localRouteGroup': [0],
        },
    ):
        """
        Get a dictionary of device pools
        :param mini: return a list of tuples of device pool info
        :return: a list of dictionary's of device pools information
        """
        try:
            return self.client.listDevicePool({"name": "%"}, returnedTags=tagfilter,)[
                "return"
            ]["devicePool"]
        except Fault as e:
            return e

    def get_device_pool(self, **args):
        """
        Get device pool parameters
        :param name: device pool name
        :return: result dictionary
        """
        try:
            return self.client.getDevicePool(**args)
        except Fault as e:
            return e

    def add_device_pool(
        self,
        name,
        date_time_group="CMLocal",
        region="Default",
        location="Hub_None",
        route_group="",
        media_resource_group_list="",
        srst="Disable",
        cm_group="Default",
        network_locale="",
    ):

        """
        Add a device pool
        :param device_pool: Device pool name
        :param date_time_group: Date time group name
        :param region: Region name
        :param location: Location name
        :param route_group: Route group name
        :param media_resource_group_list: Media resource group list name
        :param srst: SRST name
        :param cm_group: CM Group name
        :param network_locale: Network locale name
        :return: result dictionary
        """
        try:
            return self.client.addDevicePool(
                {
                    "name": name,
                    "dateTimeSettingName": date_time_group,  # update to state timezone
                    "regionName": region,
                    "locationName": location,
                    "localRouteGroup": {
                        "name": "Standard Local Route Group",
                        "value": route_group,
                    },
                    "mediaResourceListName": media_resource_group_list,
                    "srstName": srst,
                    "callManagerGroupName": cm_group,
                    "networkLocale": network_locale,
                }
            )
        except Fault as e:
            return e

    def update_device_pool(self, **args):
        """
        Update a device pools route group and media resource group list
        :param name:
        :param uuid:
        :param newName:
        :param mediaResourceGroupListName:
        :param dateTimeSettingName:
        :param callManagerGroupName:
        :param regionName:
        :param locationName:
        :param networkLocale:
        :param srstName:
        :param localRouteGroup:
        :param elinGroup:
        :param media_resource_group_list:
        :return:
        """
        try:
            return self.client.updateDevicePool(**args)
        except Fault as e:
            return e

    def delete_device_pool(self, **args):
        """
        Delete a Device pool
        :param device_pool: The name of the Device pool to delete
        :return: result dictionary
        """
        try:
            return self.client.removeDevicePool(**args)
        except Fault as e:
            return e

    def get_conference_bridges(
        self,
        tagfilter={
            "name": "",
            "description": "",
            "devicePoolName": "",
            "locationName": "",
        },
    ):
        """
        Get conference bridges
        :param mini: List of tuples of conference bridge details
        :return: results dictionary
        """
        try:
            return self.client.listConferenceBridge(
                {"name": "%"},
                returnedTags=tagfilter,
            )["return"]["conferenceBridge"]
        except Fault as e:
            return e

    def get_conference_bridge(self, name):
        """
        Get conference bridge parameters
        :param name: conference bridge name
        :return: result dictionary
        """
        try:
            return self.client.getConferenceBridge(name=name)
        except Fault as e:
            return e

    def add_conference_bridge(
        self,
        name,
        description="",
        device_pool="Default",
        location="Hub_None",
        product="Cisco IOS Enhanced Conference Bridge",
        security_profile="Non Secure Conference Bridge",
    ):
        """
        Add a conference bridge
        :param conference_bridge: Conference bridge name
        :param description: Conference bridge description
        :param device_pool: Device pool name
        :param location: Location name
        :param product: Conference bridge type
        :param security_profile: Conference bridge security type
        :return: result dictionary
        """
        try:
            return self.client.addConferenceBridge(
                {
                    "name": name,
                    "description": description,
                    "devicePoolName": device_pool,
                    "locationName": location,
                    "product": product,
                    "securityProfileName": security_profile,
                }
            )
        except Fault as e:
            return e

    def update_conference_bridge(self, **args):
        """
        Update a conference bridge
        :param name: Conference bridge name
        :param newName: New Conference bridge name
        :param description: Conference bridge description
        :param device_pool: Device pool name
        :param location: Location name
        :param product: Conference bridge type
        :param security_profile: Conference bridge security type
        :return: result dictionary
        """
        try:
            return self.client.updateConferenceBridge(**args)
        except Fault as e:
            return e

    def delete_conference_bridge(self, name):
        """
        Delete a Conference bridge
        :param name: The name of the Conference bridge to delete
        :return: result dictionary
        """
        try:
            return self.client.removeConferenceBridge(name=name)
        except Fault as e:
            return e

    def get_transcoders(
        self, tagfilter={"name": "", "description": "", "devicePoolName": ""}
    ):
        """
        Get transcoders
        :param mini: List of tuples of transcoder details
        :return: results dictionary
        """
        try:
            return self.client.listTranscoder({"name": "%"}, returnedTags=tagfilter,)[
                "return"
            ]["transcoder"]
        except Fault as e:
            return e

    def get_transcoder(self, name):
        """
        Get conference bridge parameters
        :param name: transcoder name
        :return: result dictionary
        """
        try:
            return self.client.getTranscoder(name=name)
        except Fault as e:
            return e

    def add_transcoder(
        self,
        name,
        description="",
        device_pool="Default",
        product="Cisco IOS Enhanced Media Termination Point",
    ):
        """
        Add a transcoder
        :param transcoder: Transcoder name
        :param description: Transcoder description
        :param device_pool: Transcoder device pool
        :param product: Trancoder product
        :return: result dictionary
        """
        try:
            return self.client.addTranscoder(
                {
                    "name": name,
                    "description": description,
                    "devicePoolName": device_pool,
                    "product": product,
                }
            )
        except Fault as e:
            return e

    def update_transcoder(self, **args):
        """
        Add a transcoder
        :param name: Transcoder name
        :param newName: New Transcoder name
        :param description: Transcoder description
        :param device_pool: Transcoder device pool
        :param product: Trancoder product
        :return: result dictionary
        """
        try:
            return self.client.updateTranscoder(**args)
        except Fault as e:
            return e

    def delete_transcoder(self, name):
        """
        Delete a Transcoder
        :param name: The name of the Transcoder to delete
        :return: result dictionary
        """
        try:
            return self.client.removeTranscoder(name=name)
        except Fault as e:
            return e

    def get_mtps(self, tagfilter={"name": "", "description": "", "devicePoolName": ""}):
        """
        Get mtps
        :param mini: List of tuples of transcoder details
        :return: results dictionary
        """
        try:
            return self.client.listMtp({"name": "%"}, returnedTags=tagfilter,)[
                "return"
            ]["mtp"]
        except Fault as e:
            return e

    def get_mtp(self, name):
        """
        Get mtp parameters
        :param name: transcoder name
        :return: result dictionary
        """
        try:
            return self.client.getMtp(name=name)
        except Fault as e:
            return e

    def add_mtp(
        self,
        name,
        description="",
        device_pool="Default",
        mtpType="Cisco IOS Enhanced Media Termination Point",
    ):
        """
        Add an mtp
        :param name: MTP name
        :param description: MTP description
        :param device_pool: MTP device pool
        :param mtpType: MTP Type
        :return: result dictionary
        """
        try:
            return self.client.addMtp(
                {
                    "name": name,
                    "description": description,
                    "devicePoolName": device_pool,
                    "mtpType": mtpType,
                }
            )
        except Fault as e:
            return e

    def update_mtp(self, **args):
        """
        Update an MTP
        :param name: MTP name
        :param newName: New MTP name
        :param description: MTP description
        :param device_pool: MTP device pool
        :param mtpType: MTP Type
        :return: result dictionary
        """
        try:
            return self.client.updateMtp(**args)
        except Fault as e:
            return e

    def delete_mtp(self, name):
        """
        Delete an MTP
        :param name: The name of the Transcoder to delete
        :return: result dictionary
        """
        try:
            return self.client.removeMtp(name=name)
        except Fault as e:
            return e

    def get_h323_gateways(
        self,
        tagfilter={
            "name": "",
            "description": "",
            "devicePoolName": "",
            "locationName": "",
            "sigDigits": "",
        },
    ):
        """
        Get H323 Gateways
        :param mini: List of tuples of H323 Gateway details
        :return: results dictionary
        """
        try:
            return self.client.listH323Gateway({"name": "%"}, returnedTags=tagfilter,)[
                "return"
            ]["h323Gateway"]
        except Fault as e:
            return e

    def get_h323_gateway(self, name):
        """
        Get H323 Gateway parameters
        :param name: H323 Gateway name
        :return: result dictionary
        """
        try:
            return self.client.getH323Gateway(name=name)
        except Fault as e:
            return e

    def add_h323_gateway(self, **args):
        """
        Add H323 gateway
        :param h323_gateway:
        :param description:
        :param device_pool:
        :param location:
        :param media_resource_group_list: Media resource group list name
        :param prefix_dn:
        :param sig_digits: Significant digits, 99 = ALL
        :param css:
        :param aar_css:
        :param aar_neighborhood:
        :param product:
        :param protocol:
        :param protocol_side:
        :param pstn_access:
        :param redirect_in_num_ie:
        :param redirect_out_num_ie:
        :param cld_party_ie_num_type:
        :param clng_party_ie_num_type:
        :param clng_party_nat_pre:
        :param clng_party_inat_prefix:
        :param clng_party_unknown_prefix:
        :param clng_party_sub_prefix:
        :param clng_party_nat_strip_digits:
        :param clng_party_inat_strip_digits:
        :param clng_party_unknown_strip_digits:
        :param clng_party_sub_strip_digits:
        :param clng_party_nat_trans_css:
        :param clng_party_inat_trans_css:
        :param clng_party_unknown_trans_css:
        :param clng_party_sub_trans_css:
        :return:
        """
        try:
            return self.client.addH323Gateway(**args)
        except Fault as e:
            return e

    def update_h323_gateway(self, **args):
        """

        :param name:
        :return:
        """
        try:
            return self.client.updateH323Gateway(**args)
        except Fault as e:
            return e

    def delete_h323_gateway(self, name):
        """
        Delete a H323 gateway
        :param name: The name of the H323 gateway to delete
        :return: result dictionary
        """
        try:
            return self.client.removeH323Gateway(name=name)
        except Fault as e:
            return e

    def get_route_groups(self, tagfilter={"name": "", "distributionAlgorithm": ""}):
        """
        Get route groups
        :param mini: return a list of tuples of route group details
        :return: A list of dictionary's
        """
        try:
            return self.client.listRouteGroup({"name": "%"}, returnedTags=tagfilter)[
                "return"
            ]["routeGroup"]
        except Fault as e:
            return e

    def get_route_group(self, **args):
        """
        Get route group
        :param name: route group name
        :param uuid: route group uuid
        :return: result dictionary
        """
        try:
            return self.client.getRouteGroup(**args)
        except Fault as e:
            return e

    def add_route_group(self, name, distribution_algorithm="Top Down", members=[]):
        """
        Add a route group
        :param name: Route group name
        :param distribution_algorithm: Top Down/Circular
        :param members: A list of devices to add (must already exist DUH!)
        """
        req = {
            "name": name,
            "distributionAlgorithm": distribution_algorithm,
            "members": {"member": []},
        }

        if members:
            [
                req["members"]["member"].append(
                    {
                        "deviceName": i,
                        "deviceSelectionOrder": members.index(i) + 1,
                        "port": 0,
                    }
                )
                for i in members
            ]

        try:
            return self.client.addRouteGroup(req)
        except Fault as e:
            return e

    def delete_route_group(self, **args):
        """
        Delete a Route group
        :param name: The name of the Route group to delete
        :return: result dictionary
        """
        try:
            return self.client.removeRouteGroup(**args)
        except Fault as e:
            return e

    def update_route_group(self, **args):
        """
        Update a Route group
        :param name: The name of the Route group to update
        :param distribution_algorithm: Top Down/Circular
        :param members: A list of devices to add (must already exist DUH!)
        :return: result dictionary
        """
        try:
            return self.client.updateRouteGroup(**args)
        except Fault as e:
            return e

    def get_route_lists(self, tagfilter={"name": "", "description": ""}):
        """
        Get route lists
        :param mini: return a list of tuples of route list details
        :return: A list of dictionary's
        """
        try:
            return self.client.listRouteList({"name": "%"}, returnedTags=tagfilter)[
                "return"
            ]["routeList"]
        except Fault as e:
            return e

    def get_route_list(self, **args):
        """
        Get route list
        :param name: route list name
        :param uuid: route list uuid
        :return: result dictionary
        """
        try:
            return self.client.getRouteList(**args)
        except Fault as e:
            return e

    def add_route_list(
        self,
        name,
        description="",
        cm_group_name="Default",
        route_list_enabled="true",
        run_on_all_nodes="false",
        members=[],
    ):

        """
        Add a route list
        :param name: Route list name
        :param description: Route list description
        :param cm_group_name: Route list call mangaer group name
        :param route_list_enabled: Enable route list
        :param run_on_all_nodes: Run route list on all nodes
        :param members: A list of route groups
        :return: Result dictionary
        """
        req = {
            "name": name,
            "description": description,
            "callManagerGroupName": cm_group_name,
            "routeListEnabled": route_list_enabled,
            "runOnEveryNode": run_on_all_nodes,
            "members": {"member": []},
        }

        if members:
            [
                req["members"]["member"].append(
                    {
                        "routeGroupName": i,
                        "selectionOrder": members.index(i) + 1,
                        "calledPartyTransformationMask": "",
                        "callingPartyTransformationMask": "",
                        "digitDiscardInstructionName": "",
                        "callingPartyPrefixDigits": "",
                        "prefixDigitsOut": "",
                        "useFullyQualifiedCallingPartyNumber": "Default",
                        "callingPartyNumberingPlan": "Cisco CallManager",
                        "callingPartyNumberType": "Cisco CallManager",
                        "calledPartyNumberingPlan": "Cisco CallManager",
                        "calledPartyNumberType": "Cisco CallManager",
                    }
                )
                for i in members
            ]

        try:
            return self.client.addRouteList(req)
        except Fault as e:
            return e

    def delete_route_list(self, **args):
        """
        Delete a Route list
        :param name: The name of the Route list to delete
        :param uuid: The uuid of the Route list to delete
        :return: result dictionary
        """
        try:
            return self.client.removeRouteList(**args)
        except Fault as e:
            return e

    def update_route_list(self, **args):
        """
        Update a Route list
        :param name: The name of the Route list to update
        :param uuid: The uuid of the Route list to update
        :param description: Route list description
        :param cm_group_name: Route list call mangaer group name
        :param route_list_enabled: Enable route list
        :param run_on_all_nodes: Run route list on all nodes
        :param members: A list of route groups
        :return: result dictionary
        """
        try:
            return self.client.updateRouteList(**args)
        except Fault as e:
            return e

    def get_partitions(self, tagfilter={"name": "", "description": ""}):
        """
        Get partitions
        :param mini: return a list of tuples of partition details
        :return: A list of dictionary's
        """
        try:
            return self.client.listRoutePartition(
                {"name": "%"}, returnedTags=tagfilter
            )["return"]["routePartition"]
        except Fault as e:
            return e

    def get_partition(self, **args):
        """
        Get partition details
        :param partition: Partition name
        :param uuid: UUID name
        :return: result dictionary
        """
        try:
            return self.client.getRoutePartition(**args)
        except Fault as e:
            return e

    def add_partition(self, name, description="", time_schedule_name="All the time"):
        """
        Add a partition
        :param name: Name of the partition to add
        :param description: Partition description
        :param time_schedule_name: Name of the time schedule to use
        :return: result dictionary
        """
        try:
            return self.client.addRoutePartition(
                {
                    "name": name,
                    "description": description,
                    "timeScheduleIdName": time_schedule_name,
                }
            )
        except Fault as e:
            return e

    def delete_partition(self, **args):
        """
        Delete a partition
        :param partition: The name of the partition to delete
        :return: result dictionary
        """
        try:
            return self.client.removeRoutePartition(**args)
        except Fault as e:
            return e

    def update_partition(self, **args):
        """
        Update calling search space
        :param uuid: CSS UUID
        :param name: CSS Name
        :param description:
        :param newName:
        :param timeScheduleIdName:
        :param useOriginatingDeviceTimeZone:
        :param timeZone:
        :return: result dictionary
        """
        try:
            return self.client.updateRoutePartition(**args)
        except Fault as e:
            return e

    def get_calling_search_spaces(self, tagfilter={"name": "", "description": ""}):
        """
        Get calling search spaces
        :param mini: return a list of tuples of css details
        :return: A list of dictionary's
        """
        try:
            return self.client.listCss({"name": "%"}, returnedTags=tagfilter)["return"][
                "css"
            ]
        except Fault as e:
            return e

    def get_calling_search_space(self, **css):
        """
        Get Calling search space details
        :param name: Calling search space name
        :param uuid: Calling search space uuid
        :return: result dictionary
        """
        try:
            return self.client.getCss(**css)
        except Fault as e:
            return e

    def add_calling_search_space(self, name, description="", members=[]):
        """
        Add a Calling search space
        :param name: Name of the CSS to add
        :param description: Calling search space description
        :param members: A list of partitions to add to the CSS
        :return: result dictionary
        """
        req = {
            "name": name,
            "description": description,
            "members": {"member": []},
        }
        if members:
            [
                req["members"]["member"].append(
                    {
                        "routePartitionName": i,
                        "index": members.index(i) + 1,
                    }
                )
                for i in members
            ]

        try:
            return self.client.addCss(req)
        except Fault as e:
            return e

    def delete_calling_search_space(self, **args):
        """
        Delete a Calling search space
        :param calling_search_space: The name of the partition to delete
        :return: result dictionary
        """
        try:
            return self.client.removeCss(**args)
        except Fault as e:
            return e

    def update_calling_search_space(self, **args):
        """
        Update calling search space
        :param uuid: CSS UUID
        :param name: CSS Name
        :param description:
        :param newName:
        :param members:
        :param removeMembers:
        :param addMembers:
        :return: result dictionary
        """
        try:
            return self.client.updateCss(**args)
        except Fault as e:
            return e

    def get_route_patterns(
        self, tagfilter={"pattern": "", "description": "", "uuid": ""}
    ):
        """
        Get route patterns
        :param mini: return a list of tuples of route pattern details
        :return: A list of dictionary's
        """
        try:
            return self.client.listRoutePattern(
                {"pattern": "%"},
                returnedTags=tagfilter,
            )["return"]["routePattern"]
        except Fault as e:
            return e

    def get_route_pattern(self, pattern="", uuid=""):
        """
        Get route pattern
        :param pattern: route pattern
        :param uuid: route pattern uuid
        :return: result dictionary
        """
        if uuid == "" and pattern != "":
            # Cant get pattern directly so get UUID first
            try:
                uuid = self.client.listRoutePattern(
                    {"pattern": pattern}, returnedTags={"uuid": ""}
                )
            except Fault as e:
                return e
            if "return" in uuid and uuid["return"] is not None:
                uuid = uuid["return"]["routePattern"][0]["uuid"]
                try:
                    return self.client.getRoutePattern(uuid=uuid)
                except Fault as e:
                    return e

        elif uuid != "" and pattern == "":
            try:
                return self.client.getRoutePattern(uuid=uuid)
            except Fault as e:
                return e

    def add_route_pattern(
        self,
        pattern,
        gateway="",
        route_list="",
        description="",
        partition="",
        blockEnable=False,
        patternUrgency=False,
        releaseClause="Call Rejected",
    ):
        """
        Add a route pattern
        :param pattern: Route pattern - required
        :param gateway: Destination gateway - required
        :param route_list: Destination route list - required
               Either a gateway or route list can be used at the same time
        :param description: Route pattern description
        :param partition: Route pattern partition
        :return: result dictionary
        """

        req = {
            "pattern": pattern,
            "description": description,
            "destination": {},
            "routePartitionName": partition,
            "blockEnable": blockEnable,
            "releaseClause": releaseClause,
            "useCallingPartyPhoneMask": "Default",
            "networkLocation": "OnNet",
        }

        if gateway == "" and route_list == "":
            return "Either a gateway OR route list, is a required parameter"

        elif gateway != "" and route_list != "":
            return "Enter a gateway OR route list, not both"

        elif gateway != "":
            req["destination"].update({"gatewayName": gateway})
        elif route_list != "":
            req["destination"].update({"routeListName": route_list})
        try:
            return self.client.addRoutePattern(req)
        except Fault as e:
            return e

    def delete_route_pattern(self, **args):
        """
        Delete a route pattern
        :param uuid: The pattern uuid
        :param pattern: The pattern of the route to delete
        :param partition: The name of the partition
        :return: result dictionary
        """
        try:
            return self.client.removeRoutePattern(**args)
        except Fault as e:
            return e

    def update_route_pattern(self, **args):
        """
        Update a route pattern
        :param uuid: The pattern uuid
        :param pattern: The pattern of the route to update
        :param partition: The name of the partition
        :param gateway: Destination gateway - required
        :param route_list: Destination route list - required
               Either a gateway or route list can be used at the same time
        :param description: Route pattern description
        :param partition: Route pattern partition
        :return: result dictionary
        """
        try:
            return self.client.updateRoutePattern(**args)
        except Fault as e:
            return e

    def get_media_resource_groups(self, tagfilter={"name": "", "description": ""}):
        """
        Get media resource groups
        :param mini: return a list of tuples of route pattern details
        :return: A list of dictionary's
        """
        try:
            return self.client.listMediaResourceGroup(
                {"name": "%"}, returnedTags=tagfilter
            )["return"]["mediaResourceGroup"]
        except Fault as e:
            return e

    def get_media_resource_group(self, name):
        """
        Get a media resource group details
        :param media_resource_group: Media resource group name
        :return: result dictionary
        """
        try:
            return self.client.getMediaResourceGroup(name=name)
        except Fault as e:
            return e

    def add_media_resource_group(
        self, name, description="", multicast="false", members=[]
    ):
        """
        Add a media resource group
        :param name: Media resource group name
        :param description: Media resource description
        :param multicast: Mulicast enabled
        :param members: Media resource group members
        :return: result dictionary
        """
        req = {
            "name": name,
            "description": description,
            "multicast": multicast,
            "members": {"member": []},
        }

        if members:
            [req["members"]["member"].append({"deviceName": i}) for i in members]

        try:
            return self.client.addMediaResourceGroup(req)
        except Fault as e:
            return e

    def update_media_resource_group(self, **args):
        """
        Update a media resource group
        :param name: Media resource group name
        :param description: Media resource description
        :param multicast: Mulicast enabled
        :param members: Media resource group members
        :return: result dictionary
        """
        try:
            return self.client.updateMediaResourceGroup(**args)
        except Fault as e:
            return e

    def delete_media_resource_group(self, name):
        """
        Delete a Media resource group
        :param media_resource_group: The name of the media resource group to delete
        :return: result dictionary
        """
        try:
            return self.client.removeMediaResourceGroup(name=name)
        except Fault as e:
            return e

    def get_media_resource_group_lists(self, tagfilter={"name": ""}):
        """
        Get media resource groups
        :param mini: return a list of tuples of route pattern details
        :return: A list of dictionary's
        """
        try:
            return self.client.listMediaResourceList(
                {"name": "%"}, returnedTags=tagfilter
            )["return"]["mediaResourceList"]
        except Fault as e:
            return e

    def get_media_resource_group_list(self, name):
        """
        Get a media resource group list details
        :param name: Media resource group list name
        :return: result dictionary
        """
        try:
            return self.client.getMediaResourceList(name=name)
        except Fault as e:
            return e

    def add_media_resource_group_list(self, name, members=[]):
        """
        Add a media resource group list
        :param media_resource_group_list: Media resource group list name
        :param members: A list of members
        :return:
        """
        req = {"name": name, "members": {"member": []}}

        if members:
            [
                req["members"]["member"].append(
                    {"order": members.index(i), "mediaResourceGroupName": i}
                )
                for i in members
            ]
        try:
            return self.client.addMediaResourceList(req)
        except Fault as e:
            return e

    def update_media_resource_group_list(self, **args):
        """
        Update a media resource group list
        :param name: Media resource group name
        :param description: Media resource description
        :param multicast: Mulicast enabled
        :param members: Media resource group members
        :return: result dictionary
        """
        try:
            return self.client.updateMediaResourceList(**args)
        except Fault as e:
            return e

    def delete_media_resource_group_list(self, name):
        """
        Delete a Media resource group list
        :param name: The name of the media resource group list to delete
        :return: result dictionary
        """
        try:
            return self.client.removeMediaResourceList(name=name)
        except Fault as e:
            return e

    @serialize_list
    @check_tags("listLine")
    def get_directory_numbers(
        self,
        pattern="%",
        description="%",
        route_partition="%",
        *,
        return_tags=[
            "pattern",
            "description",
            "routePartitionName",
        ],
    ) -> Union[list[dict], Fault]:
        """Get all directory numbers that match the given criteria.

        Parameters
        ----------
        pattern : str, optional
            DN pattern to match against, by default "%" which is the SQL wildcard for "any"
        description : str, optional
            Description string to match against, by default "%" which is the SQL wildcard for "any"
        route_partition : str, optional
            Route partition name to match against, by default "%" which is the SQL wildcard for "any"
        return_tags : list, optional, keyword-only
            The categories to be returned, by default [ "pattern", "description", "routePartitionName", ]. If an empty list is provided, all categories will be returned.

        Returns
        -------
        list[dict]
            A list of all directory numbers found. List will be empty if no DNs are found.
        Fault
            If an error occurs, returns the error provided by AXL.
        """
        tags = _tag_handler(return_tags)

        return _chunk_data(
            self.client.listLine,
            data_label="line",
            searchCriteria={
                "pattern": pattern,
                "description": description,
                "routePartitionName": route_partition,
            },
            returnedTags=tags,
        )

    @serialize
    @check_tags("getLine")
    def get_directory_number(
        self,
        pattern: str,
        route_partition: str,
        *,
        return_tags=["pattern", "description", "routePartitionName"],
    ) -> Union[dict, Fault]:
        """Finds the DN matching the provided pattern and Route Partition.

        Parameters
        ----------
        pattern : str
            The digits of the DN. Must be exact, no SQL wildcards.
        route_partition : str
            The Route Partition where the DN can be found. Must be exact, no SQL wildcards.
        return_tags : list, optional, keyword-only
            The categories to be returned, by default ["pattern", "description", "routePartitionName"]. If an empty list is provided, all categories will be returned.

        Returns
        -------
        dict
            If the DN is found, returns requested data.
        Fault
            If the DN is not found or an error occurs, returns the error provided by AXL.
        """
        tags = _tag_handler(return_tags)
        try:
            return self.client.getLine(
                pattern=pattern, routePartitionName=route_partition, returnedTags=tags
            )["return"]["line"]
        except Fault as e:
            return e

    def add_directory_number(
        self,
        pattern,
        partition="",
        description="",
        alerting_name="",
        ascii_alerting_name="",
        shared_line_css="",
        aar_neighbourhood="",
        call_forward_css="",
        vm_profile_name="NoVoiceMail",
        aar_destination_mask="",
        call_forward_destination="",
        forward_all_to_vm="false",
        forward_all_destination="",
        forward_to_vm="false",
    ):
        """
        Add a directory number
        :param pattern: Directory number
        :param partition: Route partition name
        :param description: Directory number description
        :param alerting_name: Alerting name
        :param ascii_alerting_name: ASCII alerting name
        :param shared_line_css: Calling search space
        :param aar_neighbourhood: AAR group
        :param call_forward_css: Call forward calling search space
        :param vm_profile_name: Voice mail profile
        :param aar_destination_mask: AAR destination mask
        :param call_forward_destination: Call forward destination
        :param forward_all_to_vm: Forward all to voice mail checkbox
        :param forward_all_destination: Forward all destination
        :param forward_to_vm: Forward to voice mail checkbox
        :return: result dictionary
        """
        try:
            return self.client.addLine(
                {
                    "pattern": pattern,
                    "routePartitionName": partition,
                    "description": description,
                    "alertingName": alerting_name,
                    "asciiAlertingName": ascii_alerting_name,
                    "voiceMailProfileName": vm_profile_name,
                    "shareLineAppearanceCssName": shared_line_css,
                    "aarNeighborhoodName": aar_neighbourhood,
                    "aarDestinationMask": aar_destination_mask,
                    "usage": "Device",
                    "callForwardAll": {
                        "forwardToVoiceMail": forward_all_to_vm,
                        "callingSearchSpaceName": call_forward_css,
                        "destination": forward_all_destination,
                    },
                    "callForwardBusy": {
                        "forwardToVoiceMail": forward_to_vm,
                        "callingSearchSpaceName": call_forward_css,
                        "destination": call_forward_destination,
                    },
                    "callForwardBusyInt": {
                        "forwardToVoiceMail": forward_to_vm,
                        "callingSearchSpaceName": call_forward_css,
                        "destination": call_forward_destination,
                    },
                    "callForwardNoAnswer": {
                        "forwardToVoiceMail": forward_to_vm,
                        "callingSearchSpaceName": call_forward_css,
                        "destination": call_forward_destination,
                    },
                    "callForwardNoAnswerInt": {
                        "forwardToVoiceMail": forward_to_vm,
                        "callingSearchSpaceName": call_forward_css,
                        "destination": call_forward_destination,
                    },
                    "callForwardNoCoverage": {
                        "forwardToVoiceMail": forward_to_vm,
                        "callingSearchSpaceName": call_forward_css,
                        "destination": call_forward_destination,
                    },
                    "callForwardNoCoverageInt": {
                        "forwardToVoiceMail": forward_to_vm,
                        "callingSearchSpaceName": call_forward_css,
                        "destination": call_forward_destination,
                    },
                    "callForwardOnFailure": {
                        "forwardToVoiceMail": forward_to_vm,
                        "callingSearchSpaceName": call_forward_css,
                        "destination": call_forward_destination,
                    },
                    "callForwardNotRegistered": {
                        "forwardToVoiceMail": forward_to_vm,
                        "callingSearchSpaceName": call_forward_css,
                        "destination": call_forward_destination,
                    },
                    "callForwardNotRegisteredInt": {
                        "forwardToVoiceMail": forward_to_vm,
                        "callingSearchSpaceName": call_forward_css,
                        "destination": call_forward_destination,
                    },
                }
            )
        except Fault as e:
            return e

    @serialize
    @operation_tag("removeLine")
    def delete_directory_number(
        self, uuid="", pattern="", route_partition=""
    ) -> Union[dict, Fault]:
        """Attempts to delete a DN.

        Parameters
        ----------
        uuid : str, optional
            The ID value of the directory number provided by AXL. If uuid is provided, all other arguments will be ignored.
        pattern : str, optional
            The exact digits of the DN. If providing a pattern, must also provide route_partition.
        route_partition : str, optional
            The Route Partition where the DN can be found.

        Returns
        -------
        dict
            If no errors occured, returns the status code provided by AXL.
        Fault
            If an error occured, returns the error thrown by AXL.

        Raises
        ------
        InvalidArguments
            when either 'uuid' or a combination of 'pattern' and 'route_partition' aren't provided.
        """
        if uuid != "":
            try:
                return self.client.removeLine(uuid=uuid)
            except Fault as e:
                return e
        elif pattern != "" and route_partition != "":
            try:
                return self.client.removeLine(
                    pattern=pattern, routePartitionName=route_partition
                )
            except Fault as e:
                return e
        else:
            raise InvalidArguments(
                "If not using a uuid, both pattern and route_partition must be provided."
            )

    @serialize
    @operation_tag("updateLine")
    def update_directory_number(
        self, uuid="", pattern="", route_partition="", **kwargs
    ):
        """
        Update a directory number
        :param pattern: Directory number
        :param partition: Route partition name
        :param description: Directory number description
        :param alerting_name: Alerting name
        :param ascii_alerting_name: ASCII alerting name
        :param shared_line_css: Calling search space
        :param aar_neighbourhood: AAR group
        :param call_forward_css: Call forward calling search space
        :param vm_profile_name: Voice mail profile
        :param aar_destination_mask: AAR destination mask
        :param call_forward_destination: Call forward destination
        :param forward_all_to_vm: Forward all to voice mail checkbox
        :param forward_all_destination: Forward all destination
        :param forward_to_vm: Forward to voice mail checkbox
        :return: result dictionary
        """
        if uuid != "":
            try:
                return self.client.updateLine(uuid=uuid, **kwargs)
            except Fault as e:
                return e
        elif pattern != "" and route_partition != "":
            try:
                return self.client.updateLine(
                    pattern=pattern, route_partition=route_partition, **kwargs
                )
            except Fault as e:
                return e
        else:
            raise InvalidArguments(
                "If not using a uuid, both pattern and route_partition must be provided."
            )

    def get_cti_route_points(self, tagfilter={"name": "", "description": ""}):
        """
        Get CTI route points
        :param mini: return a list of tuples of CTI route point details
        :return: A list of dictionary's
        """
        try:
            return self.client.listCtiRoutePoint({"name": "%"}, returnedTags=tagfilter)[
                "return"
            ]["ctiRoutePoint"]
        except Fault as e:
            return e

    def get_cti_route_point(self, **args):
        """
        Get CTI route point details
        :param name: CTI route point name
        :param uuid: CTI route point uuid
        :return: result dictionary
        """
        try:
            return self.client.getCtiRoutePoint(**args)
        except Fault as e:
            return e

    def add_cti_route_point(
        self,
        name,
        description="",
        device_pool="Default",
        location="Hub_None",
        common_device_config="",
        css="",
        product="CTI Route Point",
        dev_class="CTI Route Point",
        protocol="SCCP",
        protocol_slide="User",
        use_trusted_relay_point="Default",
        lines=[],
    ):
        """
        Add CTI route point
        lines should be a list of tuples containing the pattern and partition
        EG: [('77777', 'AU_PHONE_PT')]
        :param name: CTI route point name
        :param description: CTI route point description
        :param device_pool: Device pool name
        :param location: Location name
        :param common_device_config: Common device config name
        :param css: Calling search space name
        :param product: CTI device type
        :param dev_class: CTI device type
        :param protocol: CTI protocol
        :param protocol_slide: CTI protocol slide
        :param use_trusted_relay_point: Use trusted relay point: (Default, On, Off)
        :param lines: A list of tuples of [(directory_number, partition)]
        :return:
        """

        req = {
            "name": name,
            "description": description,
            "product": product,
            "class": dev_class,
            "protocol": protocol,
            "protocolSide": protocol_slide,
            "commonDeviceConfigName": common_device_config,
            "callingSearchSpaceName": css,
            "devicePoolName": device_pool,
            "locationName": location,
            "useTrustedRelayPoint": use_trusted_relay_point,
            "lines": {"line": []},
        }

        if lines:
            [
                req["lines"]["line"].append(
                    {
                        "index": lines.index(i) + 1,
                        "dirn": {"pattern": i[0], "routePartitionName": i[1]},
                    }
                )
                for i in lines
            ]

        try:
            return self.client.addCtiRoutePoint(req)
        except Fault as e:
            return e

    def delete_cti_route_point(self, **args):
        """
        Delete a CTI route point
        :param cti_route_point: The name of the CTI route point to delete
        :return: result dictionary
        """
        try:
            return self.client.removeCtiRoutePoint(**args)
        except Fault as e:
            return e

    def update_cti_route_point(self, **args):
        """
        Add CTI route point
        lines should be a list of tuples containing the pattern and partition
        EG: [('77777', 'AU_PHONE_PT')]
        :param name: CTI route point name
        :param description: CTI route point description
        :param device_pool: Device pool name
        :param location: Location name
        :param common_device_config: Common device config name
        :param css: Calling search space name
        :param product: CTI device type
        :param dev_class: CTI device type
        :param protocol: CTI protocol
        :param protocol_slide: CTI protocol slide
        :param use_trusted_relay_point: Use trusted relay point: (Default, On, Off)
        :param lines: A list of tuples of [(directory_number, partition)]
        :return:
        """
        try:
            return self.client.updateCtiRoutePoint(**args)
        except Fault as e:
            return e

    @serialize_list
    @check_tags("listPhone")
    def get_phones(
        self,
        name="%",
        description="%",
        css="%",
        device_pool="%",
        security_profile="%",
        *,
        return_tags=[
            "name",
            "product",
            "description",
            "protocol",
            "locationName",
            "callingSearchSpaceName",
        ],
    ) -> list[dict]:
        tags: dict = _tag_handler(return_tags)

        return _chunk_data(
            self.client.listPhone,
            data_label="phone",
            searchCriteria={
                "name": name,
                "description": description,
                "callingSearchSpaceName": css,
                "devicePoolName": device_pool,
                "securityProfileName": security_profile,
            },
            returnedTags=tags,
        )

    @serialize
    def get_phone(self, **args):
        """
        Get device profile parameters
        :param phone: profile name
        :return: result dictionary
        """
        try:
            return self.client.getPhone(**args)["return"]["phone"]
        except Fault as e:
            return e

    def add_phone(
        self,
        name,
        description="",
        product="Cisco 7941",
        device_pool="Default",
        location="Hub_None",
        phone_template="Standard 8861 SIP",
        common_device_config="",
        css="",
        aar_css="",
        subscribe_css="",
        securityProfileName="",
        lines=[],
        dev_class="Phone",
        protocol="SCCP",
        softkey_template="Standard User",
        enable_em="true",
        em_service_name="Extension Mobility",
        em_service_url=False,
        em_url_button_enable=False,
        em_url_button_index="1",
        em_url_label="Press here to logon",
        ehook_enable=1,
    ):
        """
        lines takes a list of Tuples with properties for each line EG:

                                               display                           external
            DN     partition    display        ascii          label               mask
        [('77777', 'LINE_PT', 'Jim Smith', 'Jim Smith', 'Jim Smith - 77777', '0294127777')]
        Add A phone
        :param name:
        :param description:
        :param product:
        :param device_pool:
        :param location:
        :param phone_template:
        :param common_device_config:
        :param css:
        :param aar_css:
        :param subscribe_css:
        :param lines:
        :param dev_class:
        :param protocol:
        :param softkey_template:
        :param enable_em:
        :param em_service_name:
        :param em_service_url:
        :param em_url_button_enable:
        :param em_url_button_index:
        :param em_url_label:
        :param ehook_enable:
        :return:
        """

        req = {
            "name": name,
            "description": description,
            "product": product,
            "class": dev_class,
            "protocol": protocol,
            "protocolSide": "User",
            "commonDeviceConfigName": common_device_config,
            "commonPhoneConfigName": "Standard Common Phone Profile",
            "softkeyTemplateName": softkey_template,
            "phoneTemplateName": phone_template,
            "devicePoolName": device_pool,
            "locationName": location,
            "useTrustedRelayPoint": "Off",
            "builtInBridgeStatus": "Default",
            "certificateOperation": "No Pending Operation",
            "packetCaptureMode": "None",
            "deviceMobilityMode": "Default",
            "enableExtensionMobility": enable_em,
            "callingSearchSpaceName": css,
            "automatedAlternateRoutingCssName": aar_css,
            "subscribeCallingSearchSpaceName": subscribe_css,
            "lines": {"line": []},
            "services": {"service": []},
            "vendorConfig": [{"ehookEnable": ehook_enable}],
        }

        if lines:
            [
                req["lines"]["line"].append(
                    {
                        "index": lines.index(i) + 1,
                        "dirn": {"pattern": i[0], "routePartitionName": i[1]},
                        "display": i[2],
                        "displayAscii": i[3],
                        "label": i[4],
                        "e164Mask": i[5],
                    }
                )
                for i in lines
            ]

        if em_service_url:
            req["services"]["service"].append(
                [
                    {
                        "telecasterServiceName": em_service_name,
                        "name": em_service_name,
                        "url": "http://{0}:8080/emapp/EMAppServlet?device=#DEVICENAME#&EMCC=#EMCC#".format(
                            self.cucm
                        ),
                    }
                ]
            )

        if em_url_button_enable:
            req["services"]["service"][0].update(
                {"urlButtonIndex": em_url_button_index, "urlLabel": em_url_label}
            )
        try:
            return self.client.addPhone(req)
        except Fault as e:
            return e

    def delete_phone(self, **args):
        """
        Delete a phone
        :param phone: The name of the phone to delete
        :return: result dictionary
        """
        try:
            return self.client.removePhone(**args)
        except Fault as e:
            return e

    def update_phone(self, **args):

        """
        lines takes a list of Tuples with properties for each line EG:

                                               display                           external
            DN     partition    display        ascii          label               mask
        [('77777', 'LINE_PT', 'Jim Smith', 'Jim Smith', 'Jim Smith - 77777', '0294127777')]
        Add A phone
        :param name:
        :param description:
        :param product:
        :param device_pool:
        :param location:
        :param phone_template:
        :param common_device_config:
        :param css:
        :param aar_css:
        :param subscribe_css:
        :param lines:
        :param dev_class:
        :param protocol:
        :param softkey_template:
        :param enable_em:
        :param em_service_name:
        :param em_service_url:
        :param em_url_button_enable:
        :param em_url_button_index:
        :param em_url_label:
        :param ehook_enable:
        :return:
        """
        try:
            return self.client.updatePhone(**args)
        except Fault as e:
            return e

    def get_device_profiles(
        self,
        tagfilter={
            "name": "",
            "product": "",
            "protocol": "",
            "phoneTemplateName": "",
        },
    ):
        """
        Get device profile details
        :param mini: return a list of tuples of device profile details
        :return: A list of dictionary's
        """
        try:
            return self.client.listDeviceProfile(
                {"name": "%"},
                returnedTags=tagfilter,
            )["return"]["deviceProfile"]
        except Fault as e:
            return e

    def get_device_profile(self, **args):
        """
        Get device profile parameters
        :param name: profile name
        :param uuid: profile uuid
        :return: result dictionary
        """
        try:
            return self.client.getDeviceProfile(**args)
        except Fault as e:
            return e

    def add_device_profile(
        self,
        name,
        description="",
        product="Cisco 7962",
        phone_template="Standard 7962G SCCP",
        dev_class="Device Profile",
        protocol="SCCP",
        protocolSide="User",
        softkey_template="Standard User",
        em_service_name="Extension Mobility",
        lines=[],
    ):
        """
        Add A Device profile for use with extension mobility
        lines takes a list of Tuples with properties for each line EG:

                                               display                           external
            DN     partition    display        ascii          label               mask
        [('77777', 'LINE_PT', 'Jim Smith', 'Jim Smith', 'Jim Smith - 77777', '0294127777')]
        :param name:
        :param description:
        :param product:
        :param phone_template:
        :param lines:
        :param dev_class:
        :param protocol:
        :param softkey_template:
        :param em_service_name:
        :return:
        """

        req = {
            "name": name,
            "description": description,
            "product": product,
            "class": dev_class,
            "protocol": protocol,
            "protocolSide": protocolSide,
            "softkeyTemplateName": softkey_template,
            "phoneTemplateName": phone_template,
            "lines": {"line": []},
        }

        if lines:
            [
                req["lines"]["line"].append(
                    {
                        "index": lines.index(i) + 1,
                        "dirn": {"pattern": i[0], "routePartitionName": i[1]},
                        "display": i[2],
                        "displayAscii": i[3],
                        "label": i[4],
                        "e164Mask": i[5],
                    }
                )
                for i in lines
            ]

        try:
            blah = self.client.addDeviceProfile(req)
            return blah
        except Fault as e:
            return e

    def delete_device_profile(self, **args):
        """
        Delete a device profile
        :param profile: The name of the device profile to delete
        :return: result dictionary
        """
        try:
            return self.client.removeDeviceProfile(**args)
        except Fault as e:
            return e

    def update_device_profile(self, **args):
        """
        Update A Device profile for use with extension mobility
        lines takes a list of Tuples with properties for each line EG:

                                               display                           external
            DN     partition    display        ascii          label               mask
        [('77777', 'LINE_PT', 'Jim Smith', 'Jim Smith', 'Jim Smith - 77777', '0294127777')]
        :param profile:
        :param description:
        :param product:
        :param phone_template:
        :param lines:
        :param dev_class:
        :param protocol:
        :param softkey_template:
        :param em_service_name:
        :return:
        """
        try:
            return self.client.updateDeviceProfile(**args)
        except Fault as e:
            return e

    def get_users(self, tagfilter={"userid": "", "firstName": "", "lastName": ""}):
        """
        Get users details
        :return: A list of dictionary's
        """
        skip = 0
        a = []

        def inner(skip):
            while True:
                res = self.client.listUser(
                    {"userid": "%"}, returnedTags=tagfilter, first=1000, skip=skip
                )["return"]
                skip = skip + 1000
                if res is not None and "user" in res:
                    yield res["user"]
                else:
                    break

        for each in inner(skip):
            a.extend(each)
        return a

    def get_user(self, userid):
        """
        Get user parameters
        :param user_id: profile name
        :return: result dictionary
        """
        try:
            return self.client.getUser(userid=userid)["return"]["user"]
        except Fault as e:
            return e

    def add_user(
        self,
        userid,
        lastName,
        firstName,
        presenceGroupName="Standard Presence group",
        phoneProfiles=[],
    ):
        """
        Add a user
        :param user_id: User ID of the user to add
        :param first_name: First name of the user to add
        :param last_name: Last name of the user to add
        :return: result dictionary
        """

        try:
            return self.client.addUser(
                {
                    "userid": userid,
                    "lastName": lastName,
                    "firstName": firstName,
                    "presenceGroupName": presenceGroupName,
                    "phoneProfiles": phoneProfiles,
                }
            )
        except Fault as e:
            return e

    def update_user(self, **args):
        """
        Update end user for credentials
        :param userid: User ID
        :param password: Web interface password
        :param pin: Extension mobility PIN
        :return: result dictionary
        """
        try:
            return self.client.updateUser(**args)
        except Fault as e:
            return e

    def update_user_em(
        self, user_id, device_profile, default_profile, subscribe_css, primary_extension
    ):
        """
        Update end user for extension mobility
        :param user_id: User ID
        :param device_profile: Device profile name
        :param default_profile: Default profile name
        :param subscribe_css: Subscribe CSS
        :param primary_extension: Primary extension, must be a number from the device profile
        :return: result dictionary
        """
        try:
            resp = self.client.getDeviceProfile(name=device_profile)
        except Fault as e:
            return e
        if "return" in resp and resp["return"] is not None:
            uuid = resp["return"]["deviceProfile"]["uuid"]
            try:
                return self.client.updateUser(
                    userid=user_id,
                    phoneProfiles={"profileName": {"uuid": uuid}},
                    defaultProfile=default_profile,
                    subscribeCallingSearchSpaceName=subscribe_css,
                    primaryExtension={"pattern": primary_extension},
                    associatedGroups={"userGroup": {"name": "Standard CCM End Users"}},
                )
            except Fault as e:
                return e
        else:
            return "Device Profile not found for user"

    def update_user_credentials(self, userid, password="", pin=""):
        """
        Update end user for credentials
        :param userid: User ID
        :param password: Web interface password
        :param pin: Extension mobility PIN
        :return: result dictionary
        """

        if password == "" and pin == "":
            return "Password and/or Pin are required"

        elif password != "" and pin != "":
            try:
                return self.client.updateUser(userid=userid, password=password, pin=pin)
            except Fault as e:
                return e

        elif password != "":
            try:
                return self.client.updateUser(userid=userid, password=password)
            except Fault as e:
                return e

        elif pin != "":
            try:
                return self.client.updateUser(userid=userid, pin=pin)
            except Fault as e:
                return e

    def delete_user(self, **args):
        """
        Delete a user
        :param userid: The name of the user to delete
        :return: result dictionary
        """
        try:
            return self.client.removeUser(**args)
        except Fault as e:
            return e

    def get_translations(self):
        """
        Get translation patterns
        :param mini: return a list of tuples of route pattern details
        :return: A list of dictionary's
        """
        try:
            return self.client.listTransPattern(
                {"pattern": "%"},
                returnedTags={
                    "pattern": "",
                    "description": "",
                    "uuid": "",
                    "routePartitionName": "",
                    "callingSearchSpaceName": "",
                    "useCallingPartyPhoneMask": "",
                    "patternUrgency": "",
                    "provideOutsideDialtone": "",
                    "prefixDigitsOut": "",
                    "calledPartyTransformationMask": "",
                    "callingPartyTransformationMask": "",
                    "digitDiscardInstructionName": "",
                    "callingPartyPrefixDigits": "",
                    "provideOutsideDialtone": "",
                },
            )["return"]["transPattern"]
        except Fault as e:
            return e

    def get_translation(self, pattern="", routePartitionName="", uuid=""):
        """
        Get translation pattern
        :param pattern: translation pattern to match
        :param routePartitionName: routePartitionName required if searching pattern
        :param uuid: translation pattern uuid
        :return: result dictionary
        """

        if pattern != "" and routePartitionName != "" and uuid == "":
            try:
                return self.client.getTransPattern(
                    pattern=pattern,
                    routePartitionName=routePartitionName,
                    returnedTags={
                        "pattern": "",
                        "description": "",
                        "routePartitionName": "",
                        "callingSearchSpaceName": "",
                        "useCallingPartyPhoneMask": "",
                        "patternUrgency": "",
                        "provideOutsideDialtone": "",
                        "prefixDigitsOut": "",
                        "calledPartyTransformationMask": "",
                        "callingPartyTransformationMask": "",
                        "digitDiscardInstructionName": "",
                        "callingPartyPrefixDigits": "",
                    },
                )
            except Fault as e:
                return e
        elif uuid != "" and pattern == "" and routePartitionName == "":
            try:
                return self.client.getTransPattern(
                    uuid=uuid,
                    returnedTags={
                        "pattern": "",
                        "description": "",
                        "routePartitionName": "",
                        "callingSearchSpaceName": "",
                        "useCallingPartyPhoneMask": "",
                        "patternUrgency": "",
                        "provideOutsideDialtone": "",
                        "prefixDigitsOut": "",
                        "calledPartyTransformationMask": "",
                        "callingPartyTransformationMask": "",
                        "digitDiscardInstructionName": "",
                        "callingPartyPrefixDigits": "",
                    },
                )
            except Fault as e:
                return e
        else:
            return "must specify either uuid OR pattern and partition"

    def add_translation(
        self,
        pattern,
        partition,
        description="",
        usage="Translation",
        callingSearchSpaceName="",
        useCallingPartyPhoneMask="Off",
        patternUrgency="f",
        provideOutsideDialtone="f",
        prefixDigitsOut="",
        calledPartyTransformationMask="",
        callingPartyTransformationMask="",
        digitDiscardInstructionName="",
        callingPartyPrefixDigits="",
        blockEnable="f",
        routeNextHopByCgpn="f",
    ):
        """
        Add a translation pattern
        :param pattern: Translation pattern
        :param partition: Route Partition
        :param description: Description - optional
        :param usage: Usage
        :param callingSearchSpaceName: Calling Search Space - optional
        :param patternUrgency: Pattern Urgency - optional
        :param provideOutsideDialtone: Provide Outside Dial Tone - optional
        :param prefixDigitsOut: Prefix Digits Out - optional
        :param calledPartyTransformationMask: - optional
        :param callingPartyTransformationMask: - optional
        :param digitDiscardInstructionName: - optional
        :param callingPartyPrefixDigits: - optional
        :param blockEnable: - optional
        :return: result dictionary
        """
        try:
            return self.client.addTransPattern(
                {
                    "pattern": pattern,
                    "description": description,
                    "routePartitionName": partition,
                    "usage": usage,
                    "callingSearchSpaceName": callingSearchSpaceName,
                    "useCallingPartyPhoneMask": useCallingPartyPhoneMask,
                    "patternUrgency": patternUrgency,
                    "provideOutsideDialtone": provideOutsideDialtone,
                    "prefixDigitsOut": prefixDigitsOut,
                    "calledPartyTransformationMask": calledPartyTransformationMask,
                    "callingPartyTransformationMask": callingPartyTransformationMask,
                    "digitDiscardInstructionName": digitDiscardInstructionName,
                    "callingPartyPrefixDigits": callingPartyPrefixDigits,
                    "blockEnable": blockEnable,
                }
            )
        except Fault as e:
            return e

    def delete_translation(self, pattern="", partition="", uuid=""):
        """
        Delete a translation pattern
        :param pattern: The pattern of the route to delete
        :param partition: The name of the partition
        :param uuid: Required if pattern and partition are not specified
        :return: result dictionary
        """

        if pattern != "" and partition != "" and uuid == "":
            try:
                return self.client.removeTransPattern(
                    pattern=pattern, routePartitionName=partition
                )
            except Fault as e:
                return e
        elif uuid != "" and pattern == "" and partition == "":
            try:
                return self.client.removeTransPattern(uuid=uuid)
            except Fault as e:
                return e
        else:
            return "must specify either uuid OR pattern and partition"

    def update_translation(
        self,
        pattern="",
        partition="",
        uuid="",
        newPattern="",
        description="",
        newRoutePartitionName="",
        callingSearchSpaceName="",
        useCallingPartyPhoneMask="",
        patternUrgency="",
        provideOutsideDialtone="",
        prefixDigitsOut="",
        calledPartyTransformationMask="",
        callingPartyTransformationMask="",
        digitDiscardInstructionName="",
        callingPartyPrefixDigits="",
        blockEnable="",
    ):
        """
        Update a translation pattern
        :param uuid: UUID or Translation + Partition Required
        :param pattern: Translation pattern
        :param partition: Route Partition
        :param description: Description - optional
        :param usage: Usage
        :param callingSearchSpaceName: Calling Search Space - optional
        :param patternUrgency: Pattern Urgency - optional
        :param provideOutsideDialtone: Provide Outside Dial Tone - optional
        :param prefixDigitsOut: Prefix Digits Out - optional
        :param calledPartyTransformationMask: - optional
        :param callingPartyTransformationMask: - optional
        :param digitDiscardInstructionName: - optional
        :param callingPartyPrefixDigits: - optional
        :param blockEnable: - optional
        :return: result dictionary
        """

        args = {}
        if description != "":
            args["description"] = description
        if pattern != "" and partition != "" and uuid == "":
            args["pattern"] = pattern
            args["routePartitionName"] = partition
        if pattern == "" and partition == "" and uuid != "":
            args["uuid"] = uuid
        if newPattern != "":
            args["newPattern"] = newPattern
        if newRoutePartitionName != "":
            args["newRoutePartitionName"] = newRoutePartitionName
        if callingSearchSpaceName != "":
            args["callingSearchSpaceName"] = callingSearchSpaceName
        if useCallingPartyPhoneMask != "":
            args["useCallingPartyPhoneMask"] = useCallingPartyPhoneMask
        if digitDiscardInstructionName != "":
            args["digitDiscardInstructionName"] = digitDiscardInstructionName
        if callingPartyTransformationMask != "":
            args["callingPartyTransformationMask"] = callingPartyTransformationMask
        if calledPartyTransformationMask != "":
            args["calledPartyTransformationMask"] = calledPartyTransformationMask
        if patternUrgency != "":
            args["patternUrgency"] = patternUrgency
        if provideOutsideDialtone != "":
            args["provideOutsideDialtone"] = provideOutsideDialtone
        if prefixDigitsOut != "":
            args["prefixDigitsOut"] = prefixDigitsOut
        if callingPartyPrefixDigits != "":
            args["callingPartyPrefixDigits"] = callingPartyPrefixDigits
        if blockEnable != "":
            args["blockEnable"] = blockEnable
        try:
            return self.client.updateTransPattern(**args)
        except Fault as e:
            return e

    def list_route_plan(self, pattern=""):
        """
        List Route Plan
        :param pattern: Route Plan Contains Pattern
        :return: results dictionary
        """
        try:
            return self.client.listRoutePlan(
                {"dnOrPattern": "%" + pattern + "%"},
                returnedTags={
                    "dnOrPattern": "",
                    "partition": "",
                    "type": "",
                    "routeDetail": "",
                },
            )["return"]["routePlan"]
        except Fault as e:
            return e

    def list_route_plan_specific(self, pattern=""):
        """
        List Route Plan
        :param pattern: Route Plan Contains Pattern
        :return: results dictionary
        """
        try:
            return self.client.listRoutePlan(
                {"dnOrPattern": pattern},
                returnedTags={
                    "dnOrPattern": "",
                    "partition": "",
                    "type": "",
                    "routeDetail": "",
                },
            )
        except Fault as e:
            return e

    def get_called_party_xforms(self):
        """
        Get called party xforms
        :param mini: return a list of tuples of called party transformation pattern details
        :return: A list of dictionary's
        """
        try:
            return self.client.listCalledPartyTransformationPattern(
                {"pattern": "%"},
                returnedTags={"pattern": "", "description": "", "uuid": ""},
            )["return"]["calledPartyTransformationPattern"]
        except Fault as e:
            return e

    def get_called_party_xform(self, **args):
        """
        Get called party xform details
        :param name:
        :param partition:
        :param uuid:
        :return: result dictionary
        """
        try:
            return self.client.getCalledPartyTransformationPattern(**args)
        except Fault as e:
            return e

    def add_called_party_xform(
        self,
        pattern="",
        description="",
        partition="",
        calledPartyPrefixDigits="",
        calledPartyTransformationMask="",
        digitDiscardInstructionName="",
    ):
        """
        Add a called party transformation pattern
        :param pattern: pattern - required
        :param routePartitionName: partition required
        :param description: Route pattern description
        :param calledPartyTransformationmask:
        :param dialPlanName:
        :param digitDiscardInstructionName:
        :param routeFilterName:
        :param calledPartyPrefixDigits:
        :param calledPartyNumberingPlan:
        :param calledPartyNumberType:
        :param mlppPreemptionDisabled: does anyone use this?
        :return: result dictionary
        """
        try:
            return self.client.addCalledPartyTransformationPattern(
                {
                    "pattern": pattern,
                    "description": description,
                    "routePartitionName": partition,
                    "calledPartyPrefixDigits": calledPartyPrefixDigits,
                    "calledPartyTransformationMask": calledPartyTransformationMask,
                    "digitDiscardInstructionName": digitDiscardInstructionName,
                }
            )
        except Fault as e:
            return e

    def delete_called_party_xform(self, **args):
        """
        Delete a called party transformation pattern
        :param uuid: The pattern uuid
        :param pattern: The pattern of the transformation to delete
        :param partition: The name of the partition
        :return: result dictionary
        """
        try:
            return self.client.removeCalledPartyTransformationPattern(**args)
        except Fault as e:
            return e

    def update_called_party_xform(self, **args):
        """
        Update a called party transformation
        :param uuid: required unless pattern and routePartitionName is given
        :param pattern: pattern - required
        :param routePartitionName: partition required
        :param description: Route pattern description
        :param calledPartyTransformationmask:
        :param dialPlanName:
        :param digitDiscardInstructionName:
        :param routeFilterName:
        :param calledPartyPrefixDigits:
        :param calledPartyNumberingPlan:
        :param calledPartyNumberType:
        :param mlppPreemptionDisabled: does anyone use this?
        :return: result dictionary
        :return: result dictionary
        """
        try:
            return self.client.updateCalledPartyTransformationPattern(**args)
        except Fault as e:
            return e

    def get_calling_party_xforms(self):
        """
        Get calling party xforms
        :param mini: return a list of tuples of calling party transformation pattern details
        :return: A list of dictionary's
        """
        try:
            return self.client.listCallingPartyTransformationPattern(
                {"pattern": "%"},
                returnedTags={"pattern": "", "description": "", "uuid": ""},
            )["return"]["callingPartyTransformationPattern"]
        except Fault as e:
            return e

    def get_calling_party_xform(self, **args):
        """
        Get calling party xform details
        :param name:
        :param partition:
        :param uuid:
        :return: result dictionary
        """
        try:
            return self.client.getCallingPartyTransformationPattern(**args)
        except Fault as e:
            return e

    def add_calling_party_xform(
        self,
        pattern="",
        description="",
        partition="",
        callingPartyPrefixDigits="",
        callingPartyTransformationMask="",
        digitDiscardInstructionName="",
    ):
        """
        Add a calling party transformation pattern
        :param pattern: pattern - required
        :param routePartitionName: partition required
        :param description: Route pattern description
        :param callingPartyTransformationmask:
        :param dialPlanName:
        :param digitDiscardInstructionName:
        :param routeFilterName:
        :param callingPartyPrefixDigits:
        :param callingPartyNumberingPlan:
        :param callingPartyNumberType:
        :param mlppPreemptionDisabled: does anyone use this?
        :return: result dictionary
        """
        try:
            return self.client.addCallingPartyTransformationPattern(
                {
                    "pattern": pattern,
                    "description": description,
                    "routePartitionName": partition,
                    "callingPartyPrefixDigits": callingPartyPrefixDigits,
                    "callingPartyTransformationMask": callingPartyTransformationMask,
                    "digitDiscardInstructionName": digitDiscardInstructionName,
                }
            )
        except Fault as e:
            return e

    def delete_calling_party_xform(self, **args):
        """
        Delete a calling party transformation pattern
        :param uuid: The pattern uuid
        :param pattern: The pattern of the transformation to delete
        :param partition: The name of the partition
        :return: result dictionary
        """
        try:
            return self.client.removeCallingPartyTransformationPattern(**args)
        except Fault as e:
            return e

    def update_calling_party_xform(self, **args):
        """
        Update a calling party transformation
        :param uuid: required unless pattern and routePartitionName is given
        :param pattern: pattern - required
        :param routePartitionName: partition required
        :param description: Route pattern description
        :param callingPartyTransformationMask:
        :param dialPlanName:
        :param digitDiscardInstructionName:
        :param routeFilterName:
        :param calledPartyPrefixDigits:
        :param calledPartyNumberingPlan:
        :param calledPartyNumberType:
        :param mlppPreemptionDisabled: does anyone use this?
        :return: result dictionary
        :return: result dictionary
        """
        try:
            return self.client.updateCallingPartyTransformationPattern(**args)
        except Fault as e:
            return e

    def get_sip_trunks(
        self, tagfilter={"name": "", "sipProfileName": "", "callingSearchSpaceName": ""}
    ):
        try:
            return self.client.listSipTrunk({"name": "%"}, returnedTags=tagfilter)[
                "return"
            ]["sipTrunk"]
        except Fault as e:
            return e

    def get_sip_trunk(self, **args):
        """
        Get sip trunk
        :param name:
        :param uuid:
        :return: result dictionary
        """
        try:
            return self.client.getSipTrunk(**args)
        except Fault as e:
            return e

    def update_sip_trunk(self, **args):
        """
        Update a SIP Trunk
        :param name:
        :param uuid:
        :param newName:
        :param description:
        :param callingSearchSpaceName:
        :param devicePoolName:
        :param locationName:
        :param sipProfileName:
        :param mtpRequired:

        :return:
        """
        try:
            return self.client.updateSipTrunk(**args)
        except Fault as e:
            return e

    def delete_sip_trunk(self, **args):
        try:
            return self.client.removeSipTrunk(**args)
        except Fault as e:
            return e

    def get_sip_security_profile(self, name):
        try:
            return self.client.getSipTrunkSecurityProfile(name=name)["return"]
        except Fault as e:
            return e

    def get_sip_profile(self, name):
        try:
            return self.client.getSipProfile(name=name)["return"]
        except Fault as e:
            return e

    def add_sip_trunk(self, **args):
        """
        Add a SIP Trunk
        :param name:
        :param description:
        :param product:
        :param protocol:
        :param protocolSide:
        :param callingSearchSpaceName:
        :param devicePoolName:
        :param securityProfileName:
        :param sipProfileName:
        :param destinations: param destination:
        :param runOnEveryNode:

        :return:
        """
        try:
            return self.client.addSipTrunk(**args)
        except Fault as e:
            return e

    def list_process_nodes(self):
        try:
            return self.client.listProcessNode(
                {"name": "%", "processNodeRole": "CUCM Voice/Video"},
                returnedTags={"name": ""},
            )["return"]["processNode"]
        except Fault as e:
            return e

    def add_call_manager_group(self, name, members):
        """
        Add call manager group
        :param name: name of cmg
        :param members[]: array of members
        :return: result dictionary
        """

        try:
            return self.client.addCallManagerGroup({"name": name, "members": members})
        except Fault as e:
            return e

    def get_call_manager_group(self, name):
        """
        Get call manager group
        :param name: name of cmg
        :return: result dictionary
        """
        try:
            return self.client.getCallManagerGroup(name=name)
        except Fault as e:
            return e

    def get_call_manager_groups(self):
        """
        Get call manager groups
        :param name: name of cmg
        :return: result dictionary
        """
        try:
            return self.client.listCallManagerGroup(
                {"name": "%"}, returnedTags={"name": ""}
            )["return"]["callManagerGroup"]
        except Fault as e:
            return e

    def update_call_manager_group(self, **args):
        """
        Update call manager group
        :param name: name of cmg
        :return: result dictionary
        """
        try:
            return self.client.listCallManagerGroup({**args}, returnedTags={"name": ""})
        except Fault as e:
            return e

    def delete_call_manager_group(self, name):
        """
        Delete call manager group
        :param name: name of cmg
        :return: result dictionary
        """
        try:
            return self.client.removeCallManagerGroup({"name": name})
        except Fault as e:
            return e


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


def _tag_serialize_filter(tags: Union[list, dict], data: dict) -> dict:
    """[summary]

    Parameters
    ----------
    tags : Union[list, dict]
        [description]
    data : dict
        [description]

    Returns
    -------
    dict
        [description]
    """
    working_data = data.copy()
    for tag, value in data.items():
        if tag not in tags and len(tags) > 0 and value is None:
            working_data.pop(tag, None)
        elif type(value) == dict and "_value_1" in value:
            working_data[tag] = value["_value_1"]
    return working_data


def _chunk_data(axl_request: Callable, data_label: str, **kwargs) -> Union[list, Fault]:
    skip = 0
    recv: dict = dict()
    data: list = []

    while recv is not None:
        try:
            recv = axl_request(**kwargs, first=1000, skip=skip)["return"]
        except Fault as e:
            return e
        if recv is not None:
            data.extend(recv[data_label])
            skip += 1000
    return data
