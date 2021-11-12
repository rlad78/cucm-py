from typing import Sequence
import json

from zeep.exceptions import Fault


class _ServerError(Exception):
    def __init__(self, server: str, *args: object) -> None:
        self.server = server
        super().__init__(*args)


class AXLClassException(Exception):
    pass


class URLInvalidError(_ServerError):
    def __str__(self) -> str:
        return f"{self.server} is not a valid URL."


class UCMInvalidError(_ServerError):
    def __str__(self) -> str:
        return f"{self.server} is not a valid UCM server."


class UCMConnectionFailure(_ServerError):
    def __str__(self) -> str:
        return f"Could not connect to {self.server}, please check your connection or try again."


class UCMNotFoundError(_ServerError):
    def __str__(self) -> str:
        return f"Could not locate {self.server}, please check that the URL is correct."


class AXLInvalidUrlError(UCMInvalidError):
    pass


class AXLInvalidCredentials(_ServerError):
    def __init__(self, server: str, username: str, *args: object) -> None:
        self.username = username
        super().__init__(server, *args)

    def __str__(self) -> str:
        return f"Credentials not accepted for {self.username} at {self.server}"


class AXLNotFoundError(UCMNotFoundError):
    def __str__(self) -> str:
        return f"Could not find AXL API at {self.server}, is the service activated?"


class AXLConnectionFailure(UCMConnectionFailure):
    pass


class UCMException(Exception):
    def __init__(self, err_cause=None, *args: object) -> None:
        self.err = err_cause
        super().__init__(*args)

    def __str__(self) -> str:
        if self.err is None:
            return "An unknown issue occured when trying to connect to UCM."
        else:
            return f"An error occured when trying to connect to UCM: {self.err}"


class AXLException(UCMException):
    def __str__(self) -> str:
        if self.err is None:
            return "An unknown issue occured when trying to connect to the AXL API."
        else:
            return f"An error occured when trying to connect to the AXL API: {self.err}"


class AXLFault(Exception):
    """Exception that handles Zeep Fault exceptions so users don't have to import Fault from Zeep."""

    def __init__(self, zeep_fault: Fault, *args: object) -> None:
        self.message = zeep_fault.message
        self.subcodes = zeep_fault.subcodes
        self.actor = zeep_fault.actor
        self.code = zeep_fault.code
        self.detail = zeep_fault.detail
        super().__init__(*args)

    def __str__(self) -> str:
        return self.message


class WSDLException(Exception):
    pass


class WSDLInvalidArgument(Exception):
    def __init__(self, argument: str, element_name: str, *args: object) -> None:
        self.arg = argument
        self.element = element_name
        super().__init__(*args)

    def __str__(self) -> str:
        return f"'{self.arg}' is not a valid argument for {self.element}"


class WSDLMissingArguments(Exception):
    def __init__(self, arguments: Sequence, element_name: str, *args: object) -> None:
        self.arguments = arguments
        self.element = element_name
        super().__init__(*args)

    def __str__(self) -> str:
        return f"The following arguments are missing from {self.element}: {', '.join(self.arguments)}"


def _list_options(options: Sequence) -> str:
    o_strings: list[str] = []
    for i, option in enumerate(options):
        if type(option) == str:
            o_strings.append(f"{i+1}) '{option}'")
        elif type(option) == list:
            o_strings.append(f"{i+1}) " + ", ".join([f"'{o}'" for o in option]))
    return "\n".join(o_strings)


class WSDLChoiceException(WSDLMissingArguments):
    def __str__(self) -> str:
        return f"For {self.element}, you must choose only ONE of the following:\n{_list_options(self.arguments)}"


class WSDLDrillDownException(WSDLInvalidArgument):
    def __init__(
        self, argument: str, structure: dict, element_name: str, *args: object
    ) -> None:
        try:
            self.structure = json.dumps(structure, indent=2)
        except TypeError:
            self.structure = str(structure)

        super().__init__(argument, element_name, *args)

    def __str__(self) -> str:
        return f"'{self.arg}' in {self.element} must be a dict of the following format:\n\n{self.structure}"


class WSDLValueOnlyException(WSDLInvalidArgument):
    def __str__(self) -> str:
        return f"'{self.arg}' in {self.element} should only be a single value."


class TagNotValid(Exception):
    def __init__(
        self, tag: str, valid_tags: list[str], *args, func=None, elem_name=""
    ) -> None:
        self.tag = tag
        self.func = func
        self.element = elem_name
        self.valid_tags = valid_tags
        super().__init__(*args)

    def __str__(self) -> str:
        if self.func is not None:
            return f"'{self.tag}; is not a valid return tag for {self.func.__name__}(). Valid tags are:\n{self.valid_tags}"
        elif self.element:
            return f"'{self.tag}; is not a valid return tag for {self.element}. Valid tags are:\n{self.valid_tags}"
        else:
            return f"Invalid tag encountered: '{self.tag}'"


class DumbProgrammerException(Exception):
    pass


class InvalidArguments(Exception):
    pass


class UDSConnectionError(_ServerError):
    def __str__(self) -> str:
        return f"Could not connect to CUCM UDS service at {self.server}"


class UDSParseError(Exception):
    def __init__(self, url: str, wanted: str, xml_text: str, *args: object) -> None:
        if "cucm-uds" in url:
            self.access_point = "cucm-uds" + url.split("cucm-uds")[-1]
        else:
            raise DumbProgrammerException(f"Malformed cucm-uds URI: {url}")
        self.wanted = wanted
        self.xml = xml_text
        super().__init__(*args)

    def __str__(self) -> str:
        return f"Could not find '{self.wanted}' at {self.access_point}"


class UCMVersionError(_ServerError):
    def __init__(self, server: str, version: str, *args: object) -> None:
        self.version = version
        super().__init__(server, *args)

    def __str__(self) -> str:
        return f"The UCM server at {self.server} has an unsupported version '{self.version}'"


class UCMVersionInvalid(Exception):
    def __init__(self, version: str) -> None:
        self.version = version
        super().__init__()

    def __str__(self) -> str:
        return f"An invalid CUCM version was provided: {self.version}"
