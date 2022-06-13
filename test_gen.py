from ciscoaxl import axl
from ciscoaxl.wsdl import get_tree
from ciscoaxl.pydantic_generate import generate_return_models

ucm = axl("rcarte4", "CUArfCU@93", "ucm-01.clemson.edu", "11.5", True)
m = generate_return_models(ucm._zeep, "getPhone")
print(m["RPhone"])
