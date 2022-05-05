import pytest
from cucm import Axl, get_credentials
from cucm.axl.exceptions import *
from cucm.debug import get_url_and_port


class TestAPI:
    ucm = Axl(*get_credentials(), *get_url_and_port())

    def test_standard_get(self):
        result = self.ucm.get_phone(name="BOTRCARTE4")
        assert result["description"] == "Jabber-Android-RCARTE4"

    def test_correct_tags(self):
        result = self.ucm.get_phone(name="BOTRCARTE4", return_tags=["devicePoolName"])
        assert result["devicePoolName"] == "Clemson Campus A"

    def test_invalid_tag(self):
        with pytest.raises(TagNotValid):
            self.ucm.get_phone(name="BOTRCARTE4", return_tags=["notRealTag"])

    def test_mixed_tags(self):
        with pytest.raises(TagNotValid):
            self.ucm.get_phone(
                name="BOTRCARTE4", return_tags=["description", "notRealTag"]
            )

        with pytest.raises(TagNotValid):
            self.ucm.get_phone(
                name="BOTRCARTE4", return_tags=["notRealTag", "description"]
            )

    def test_default_tags(self):
        result = self.ucm.get_ldap_dir()
        assert result[0]["name"] == "cuid"
        assert result[0].get("scheduleUnit", None) is None

    def test_all_return_tags(self):
        result = self.ucm.get_ldap_dir(return_tags=[])
        assert result[0]["name"] == "cuid"
        assert result[0].get("scheduleUnit", None) == "DAY"
