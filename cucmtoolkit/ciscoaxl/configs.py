from pathlib import Path
from zeep.exceptions import Fault

ROOT_DIR: Path = Path(__file__).parent
USERNAME_MAGIC_KEY: str = "73q0eWFaIE2JJw8FMNeX"
URL_MAGIC_KEY: str = "8Cu16DGzNvunSsDNOTrO"
DUMMY_KEY: str = "xlGoVnofkKjNSgnwA9Z7"

DISABLE_FAULT_HANDLER = False
DISABLE_SERIALIZER = False


def turn_off_fault_handler() -> None:
    global DISABLE_FAULT_HANDLER
    DISABLE_FAULT_HANDLER = True


def turn_off_serializer() -> None:
    global DISABLE_SERIALIZER
    DISABLE_SERIALIZER = True
