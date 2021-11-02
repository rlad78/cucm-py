from cucm.axl import Axl
from xmlschema import XMLSchema
from pathlib import Path

ucm = Axl("rcarte4", input("enter password pls: "), "ucm-01.clemson.edu", "11.5")
factory = ucm.client._client.type_factory("ns0")
phone_returned_tags = factory.RPhone(name="?", description="?")

# we need to get EVERYTHING to make all the methods
soap_path = (
    Path(__file__).parent
    / "cucmtoolkit"
    / "ciscoaxl"
    / "schema"
    / "11.5"
    / "AXLSoap.xsd"
)
scheme = XMLSchema(str(soap_path))
