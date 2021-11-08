from typing import Tuple
import keyring
import keyring.errors
from cucm.axl.configs import *
from stdiomask import getpass


def get_credentials(enable_manual_entry=True, quiet=True) -> Tuple[str, str]:
    if (username := keyring.get_password("cucm-py", USERNAME_MAGIC_KEY)) is None:
        if enable_manual_entry:
            return credentials_from_input(quiet)
        else:
            return "", ""
    elif (password := keyring.get_password("cucm-py", username)) is None:
        if enable_manual_entry:
            return credentials_from_input(quiet)
        else:
            return username, ""
    else:
        return username, password


def credentials_from_input(quiet=True) -> Tuple[str, str]:
    username = input("CUCM username: ")
    password = getpass(prompt="CUCM password: ")
    write_credentials(username, password)
    if not quiet:
        print("Writing ENCRYPTED passwords to system keyring")
    return username, password


def write_credentials(username: str, password: str, quiet=True) -> None:
    keyring.set_password("cucm-py", USERNAME_MAGIC_KEY, username)
    keyring.set_password("cucm-py", username, password)


def delete_credentials() -> None:
    username, password = get_credentials(enable_manual_entry=False)
    if username:
        try:
            keyring.delete_password("cucm-py", USERNAME_MAGIC_KEY)
        except keyring.errors.PasswordDeleteError:
            print("could not delete username key")
    if password:
        try:
            keyring.delete_password("cucm-py", username)
        except keyring.errors.PasswordDeleteError:
            print("could not delete password key")
