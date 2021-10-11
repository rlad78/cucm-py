from cucmtoolkit import axl, get_credentials

username, password = get_credentials()
server = "ucm-01.clemson.edu"
version = "11.5"

ucm = axl(username, password, server, version)
