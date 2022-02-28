# %%
from cucm.axl.wsdl import *


# %%
from cucm import Axl, get_credentials

username, password = get_credentials()
server = "ucm-01.clemson.edu"
dummy_sql = "select name,tkmodel from TypeProduct"

ucm = Axl(username, password, server)


# %%
