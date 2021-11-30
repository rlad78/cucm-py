from cucm.axl import Axl, get_credentials
from cucm.axl.exceptions import (
    URLInvalidError,
    UCMInvalidError,
    UCMConnectionFailure,
    UCMNotFoundError,
    AXLInvalidUrlError,
    AXLInvalidCredentials,
    AXLNotFoundError,
    AXLConnectionFailure,
    UCMVersionError,
    UCMVersionInvalid,
    AXLClassException,
    WSDLException,
)
from cucm.axl.wsdl import print_element_layout
import keyring
import sys, os


VALIDATION_EXCEPTIONS = (
    URLInvalidError,
    UCMInvalidError,
    UCMConnectionFailure,
    UCMNotFoundError,
    AXLInvalidUrlError,
    AXLInvalidCredentials,
    AXLNotFoundError,
    AXLConnectionFailure,
    UCMVersionError,
    UCMVersionInvalid,
)


def set_url_and_port() -> Axl:
    valid_ucm = None
    while valid_ucm is None:
        if not (weburl := keyring.get_password("cucm-py", "webaddr")):
            new_weburl = input(
                "Please enter your CUCM URL (use ':[port]' if different than ':8443'): "
            )
            if (new_port := new_weburl.split(":")[-1]).isnumeric():
                new_weburl = new_weburl[:-1]
                port = new_port
            else:
                port = "8443"
            try:
                valid_ucm = Axl(
                    *get_credentials(), cucm=new_weburl, port=port, verbose=True
                )
                keyring.set_password("cucm-py", "webaddr", new_weburl)
                keyring.set_password("cucm-py", "port", port)
            except VALIDATION_EXCEPTIONS as e:
                print(f"\nThat URL didn't work ({e.__name__})...please try again.")
        else:
            port = keyring.get_password("cucm-py", "port")
            try:
                valid_ucm = Axl(*get_credentials(), cucm=weburl, port=port)
            except VALIDATION_EXCEPTIONS as e:
                if (
                    input(
                        f"Stored URL '{weburl}:{port}' did not work ({e.__name__}).\nWant to try another? [y/n]: "
                    ).lower()
                    == "y"
                ):
                    keyring.set_password("cucm-py", "webaddr", "")
                    keyring.set_password("cucm-py", "port", "")
                    continue
                else:
                    raise Exception("Could not connect to UCM AXL service")
    return valid_ucm


def get_url_and_port() -> tuple[str, str]:
    url = keyring.get_password("cucm-py", "webaddr")
    port = keyring.get_password("cucm-py", "port")
    if not url:
        raise Exception("Need to run 'poetry run test_connect' to save URL first")
    return url, port


def clear_url_and_port() -> None:
    keyring.set_password("cucm-py", "webaddr", "")
    keyring.set_password("cucm-py", "port", "")
    print("URL and port cleared")


def axl_connect() -> None:
    ucm = set_url_and_port()
    print(ucm.cucm, f"v{ucm.cucm_version}")


def print_axl_tree() -> None:
    ucm = set_url_and_port()
    if len(sys.argv) < 2:
        print(
            "USAGE: poetry run show_tree [AXL_METHOD] [AXL_METHOD_2] [AXL_METHOD_3] ..."
        )
    else:
        for n, method in enumerate(sys.argv[1:]):
            if n > 0:
                input("\nPress [enter] to continue or [ctrl + c] to stop.")
                print("\n", "=" * (os.get_terminal_size().columns - 1), sep="")

            try:
                print("")  # newline
                ucm.print_axl_arguments(method)
            except AXLClassException as e:
                print(f"[ERROR]({method}): {e.__str__}")


def print_soap_tree() -> None:
    ucm = set_url_and_port()
    if len(sys.argv) < 2:
        print(
            "USAGE: poetry run show_tree [AXL_METHOD] [AXL_METHOD_2] [AXL_METHOD_3] ..."
        )
    else:
        for n, element in enumerate(sys.argv[1:]):
            if n > 0:
                input("\nPress [enter] to continue or [ctrl + c] to stop.")
                print("\n", "=" * (os.get_terminal_size().columns - 1), sep="")

            try:
                print("")  # newline
                print_element_layout(ucm.zeep, element, show_required=True)
            except WSDLException as e:
                print(f"[ERROR]({element}): {e.__str__}")
