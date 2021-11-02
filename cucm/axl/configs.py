from pathlib import Path
from zeep.exceptions import Fault

ROOT_DIR: Path = Path(__file__).parent
CUCM_LATEST_VERSION: str = "14.0"

USERNAME_MAGIC_KEY: str = "73q0eWFaIE2JJw8FMNeX"
URL_MAGIC_KEY: str = "8Cu16DGzNvunSsDNOTrO"
DUMMY_KEY: str = "xlGoVnofkKjNSgnwA9Z7"

DISABLE_SERIALIZER = False
DISABLE_CHECK_TAGS = False


def turn_off_serializer() -> None:
    global DISABLE_SERIALIZER
    DISABLE_SERIALIZER = True


def turn_off_tags_checker() -> None:
    global DISABLE_CHECK_TAGS
    DISABLE_CHECK_TAGS = True
