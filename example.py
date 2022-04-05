# %%
from cucm.axl.wsdl import *
from cucm.debug import identify_bad_tag


# %%
from cucm import Axl, get_credentials

username, password = get_credentials()
server = "ucm-01.clemson.edu"
dummy_sql = "select name,tkmodel from TypeProduct"

ucm = Axl(username, password, server)


# %%
identify_bad_tag(ucm, "getGatewaySccpEndpoints", {"name": "AN1CB0CA1430000"})
