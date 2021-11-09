# %%
from cucm.axl.wsdl import *


# %%
from cucm import Axl, get_credentials

username, password = get_credentials()
server = "ucm-01.clemson.edu"
dummy_sql = "select name,tkmodel from TypeProduct"

ucm = Axl(username, password, server)
# %%
ele = AXLElement(ucm.zeep.get_element("ns0:addPhone"))
ele.needed_only().print_tree(show_required=True)

# %%
