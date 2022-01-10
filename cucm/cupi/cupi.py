from json.decoder import JSONDecodeError
from requests.auth import HTTPBasicAuth
from requests.models import HTTPError, Response
from requests.sessions import Session
from .exceptions import APIError, CupiHTTPError, DNAlreadyExists, UserNotFound


class Cupi:
    def __init__(self, username: str, password: str, unity_url: str) -> None:
        self.username = username
        self.password = password
        self.api = f"https://{unity_url}/vmrest/"

        s = Session()
        s.auth = HTTPBasicAuth(username, password)
        s.headers = {"Accept": "application/json"}
        self.session = s

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if hasattr(self, "session"):
            self.session.close()

    def _get(self, uri: str, params: dict = None) -> dict:
        if params is None:
            recv = self.session.get(f"{self.api}{uri}")
        else:
            recv = self.session.get(f"{self.api}{uri}", params=params)

        return resp(recv)

    def _post(self, uri: str, params: dict = None, body: dict = None) -> dict:
        args = {"url": f"{self.api}{uri}"}
        if params is not None:
            args["params"] = params
        if body is not None:
            args["json"] = body

        recv = self.session.post(**args)
        return resp(recv)

    def _put(self, uri: str, params: dict = None, body: dict = None) -> dict:
        args = {"url": f"{self.api}{uri}"}
        if params is not None:
            args["params"] = params
        if body is not None:
            args["json"] = body

        recv = self.session.put(**args)
        return resp(recv)

    def get_user(self, username: str) -> dict:
        query = {"query": f"(alias is {username.strip()})"}
        results = self._get("users", query)

        if (found_users := results.get("@total", None)) is None:
            raise APIError(results)
        if found_users == "0":
            return {}
        else:
            return results["User"]

    def import_user(self, username: str, dn: str, user_template: str) -> dict:
        # get user LDAP info
        uri = "import/users/ldap"
        ldap_user = self._get(uri, {"query": f"(alias is {username})"})

        if (found_users := ldap_user.get("@total", None)) is None:
            raise APIError(ldap_user)
        elif found_users == "0":
            raise UserNotFound(username, f"{uri}?query=(alias is {username})")

        body: dict = ldap_user["ImportUser"]
        body.update(
            {
                "phoneNumber": dn,
                "dtmfAccessId": dn,
            }
        )

        return self._post(uri, {"templateAlias": user_template}, body)

    def update_pin(self, username: str, pin: str, *, user_must_change=False) -> dict:
        user = self.get_user(username)
        uri = f"users/{user['ObjectId']}/credential/pin"

        body = {"Credentials": pin, "CredMustChange": user_must_change}

        return self._put(uri, body=body)

    def update_dn(self, username: str, dn: str) -> dict:
        # check if dn is in use
        results = self._get("users", {"query": f"(DtmfAccessId is {dn})"})
        if int(results["@total"]) == 1:
            raise DNAlreadyExists(dn, results["User"]["Alias"])

        user = self.get_user(username)
        uri = f"users/{user['ObjectId']}"
        body = {"DtmfAccessId": dn}

        return self._put(uri, body=body)


def resp(recv: Response) -> dict:
    try:
        recv.raise_for_status()
    except HTTPError:
        raise CupiHTTPError(recv)

    try:
        return recv.json()
    except JSONDecodeError:
        return {"status_code": recv.status_code, "response": recv.text}
