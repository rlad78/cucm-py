from typing import Callable


class _ServerError(Exception):
    def __init__(self, server: str, *args: object) -> None:
        self.server = server
        super().__init__(*args)


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


class WSDLException(Exception):
    pass


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
        return f"An invalid CUCM version was supplied: {self.version}"
