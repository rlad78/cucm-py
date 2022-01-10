import json
from json.decoder import JSONDecodeError
from requests.models import Response


class UserNotFound(Exception):
    def __init__(self, username: str, query: str, *args: object) -> None:
        self.username = username
        self.query = query
        super().__init__(*args)

    def __str__(self) -> str:
        return f"Could not find user '{self.username}' at {self.query}"


class APIError(Exception):
    def __init__(self, recv: Response, *args: object) -> None:
        self.recv: Response = recv
        super().__init__(*args)

    def __str__(self) -> str:
        return (
            f"An unknown error occured: {self.recv.status_code} - {self.recv.content}"
        )


class CupiHTTPError(APIError):
    def __str__(self) -> str:
        s = f"{self.recv.status_code} Client Error: {self.recv.reason} for url: {self.recv.url}"
        try:
            details = self.recv.json()
            s += "\n" + json.dumps(details, indent=2)
        except JSONDecodeError:
            pass
        return s
