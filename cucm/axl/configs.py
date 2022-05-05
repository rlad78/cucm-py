from pathlib import Path

AXL_DIR: Path = Path(__file__).parent
CUCM_LATEST_VERSION: str = "14.0"

USERNAME_MAGIC_KEY: str = "73q0eWFaIE2JJw8FMNeX"
URL_MAGIC_KEY: str = "8Cu16DGzNvunSsDNOTrO"
DUMMY_KEY: str = "xlGoVnofkKjNSgnwA9Z7"

DISABLE_SERIALIZER = False
# DISABLE_CHECK_TAGS = False
DISABLE_CHECK_ARGS = False
AUTO_INCLUDE_UUIDS = True


def turn_off_serializer() -> None:
    global DISABLE_SERIALIZER
    DISABLE_SERIALIZER = True


# def turn_off_tags_checker() -> None:
#     global DISABLE_CHECK_TAGS
#     DISABLE_CHECK_TAGS = True


def turn_off_args_checker() -> None:
    global DISABLE_CHECK_ARGS
    DISABLE_CHECK_ARGS = True


def auto_include_uuid(state: bool) -> None:
    """By default, UUIDs will always be included in every returned object that supports them. If you wish to turm this off, run this function with a False value as the input. If you want to turn this back on at any point, run this again with True as the input.

    When auto-including UUIDs is off, you can still get UUIDs by listing them as a tag in `return_tags` for methods that support it.

    Args:
        state (bool): True for auto-including UUIDs in results (default), or False for only including them if explicitly asked for in return_tags.
    """
    global AUTO_INCLUDE_UUIDS
    AUTO_INCLUDE_UUIDS = False
