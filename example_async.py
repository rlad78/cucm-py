# %%
from cucm.axl.wsdl import *


# %%
import asyncio
from cucm.axl.asyncaxl import AsyncAXL
from cucm import get_credentials
from cucm.axl.asyncaxl import APICall

username, password = get_credentials()
server = "ucm-01.clemson.edu"
dummy_sql = "select name,tkmodel from TypeProduct"

ucm = AsyncAXL(username, password, server)


# %%
print(asyncio.run(ucm.get_phone("BOTRCARTE4", return_tags=["description", "model"])))