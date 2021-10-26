# %%
from cucmtoolkit.ciscoaxl.wsdl import *


# %%
from cucmtoolkit import axl, get_credentials

username, password = get_credentials()
server = "ucm-01.clemson.edu"
dummy_sql = "select name,tkmodel from TypeProduct"

ucm = axl(username, password, server)
# %%
