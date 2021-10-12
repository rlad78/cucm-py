# %%
from cucmtoolkit import axl, get_credentials

username, password = get_credentials()
server = "ucm-01.clemson.edu"
version = "11.5"
dummy_sql = "select registrationdynamic.datetimestamp, registrationdynamic.fkdevice, device.pkid , device.name  from registrationdynamic inner join device on registrationdynamic.fkdevice=device.pkid where device.tkmodel='503'"

ucm = axl(username, password, server, version)


# %%
