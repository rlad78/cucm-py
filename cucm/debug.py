from typing import Union
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
from cucm.axl.wsdl import print_element_layout, fix_return_tags, get_return_tags
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


def identify_bad_tag(ucm: Axl, element: str, demo_data: dict) -> None:
    tags = fix_return_tags(
        z_client=ucm.zeep, element_name=element, tags=get_return_tags(ucm.zeep, element)
    )

    class TestingException(Exception):
        def __init__(
            self,
            chain: Union[str, list[str]],
            bottom_exc: object,
            result: dict = None,
            *args: object,
        ) -> None:
            if type(chain) == str:
                self.chain = [chain]
            elif type(chain) == list:
                self.chain = chain
            self.bottom_exc = bottom_exc
            self.result = None
            super().__init__(*args)

        def __str__(self) -> str:
            if self.result is None:
                return (
                    f"Issue caused by {' -> '.join(self.chain)}"
                    + "\n"
                    + str(self.bottom_exc)
                )
            else:
                return (
                    f"Issue caused by {' -> '.join(self.chain)}"
                    + "\n"
                    + str(self.bottom_exc + "\n" + self.result)
                )

    working_dict = {}

    def test_tags(t: dict, level_dict: dict, level=0):
        for k, v in t.items():
            if type(v) == dict:
                level_dict[k] = {}
                try:
                    test_tags(t[k], level_dict[k], level + 1)
                except Exception as e:
                    if not isinstance(e, TestingException):
                        raise e
                    elif level == 0:
                        e: TestingException
                        raise TestingException(
                            chain=[k] + e.chain,
                            bottom_exc=e.bottom_exc,
                            result=working_dict,
                        )
                    else:
                        e: TestingException
                        raise TestingException(
                            chain=[k] + e.chain, bottom_exc=e.bottom_exc
                        )
            else:
                level_dict[k] = v
                try:
                    ucm._base_soap_call(
                        element, {**demo_data, "returnedTags": working_dict}, []
                    )
                except Exception as e:
                    raise TestingException(f"{k}: {v}", e)

    test_tags(tags[0], working_dict)
    print("Tags working properly")
