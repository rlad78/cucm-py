from cucm.axl import Axl, get_credentials
from cucm.axl.exceptions import AXLClassException, UCMException, WSDLException
from cucm.axl.wsdl import print_element_layout
import keyring
import sys, os


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
            except UCMException as e:
                print(f"\nThat URL didn't work ({e.err})...please try again.")
        else:
            port = keyring.get_password("cucm-py", "port")
            try:
                valid_ucm = Axl(*get_credentials(), cucm=weburl, port=port)
            except UCMException as e:
                if (
                    input(
                        f"Stored URL '{weburl}:{port}' did not work ({e.err}).\nWant to try another? [y/n]: "
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


ucm = set_url_and_port()


def axl_connect() -> None:
    print(ucm.cucm, f"v{ucm.cucm_version}")


def print_axl_tree() -> None:
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
