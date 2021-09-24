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
