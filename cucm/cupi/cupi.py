from requests.auth import HTTPBasicAuth
from requests.sessions import Session


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

    def get_user(self, username: str) -> dict:
        query = {"query": f"(alias is {username.strip()})"}
        recv = self.session.get(self.api + "users", params=query)
        results = recv.json()

        if results["@total"] == "0":
            return {}
        else:
            return results["User"]
