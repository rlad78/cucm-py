import pytest
from cucm import Axl, get_credentials
from cucm.axl.exceptions import *
from cucm.debug import get_url_and_port


class TestConnections:
    def test_valid_cucm(self):
        url, port = get_url_and_port()
        assert Axl(*get_credentials(), cucm=url, port=port).cucm == url

    def test_bad_url(self):
        with pytest.raises(URLInvalidError):
            Axl(*get_credentials(), "not a url silly")

    def test_non_ucm_server(self):
        with pytest.raises(UCMInvalidError):
            Axl(*get_credentials(), "github.com", port=443)

    def test_non_exist_server(self):
        with pytest.raises(UCMNotFoundError):
            Axl(*get_credentials(), "notanactivedomain.net", port=80)

    def test_invalid_port(self):
        url, _ = get_url_and_port()
        with pytest.raises(UCMNotFoundError):
            Axl(*get_credentials(), cucm=url, port=22)

    def test_invalid_credentials(self):
        url, port = get_url_and_port()
        with pytest.raises(AXLInvalidCredentials):
            Axl("notreal", "notreal", cucm=url, port=port)
