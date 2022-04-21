from dataclasses import dataclass, field, InitVar
from typing import Callable, Union, ClassVar
from collections.abc import Sequence
from cucm.utils import Empty
from cucm.axl.exceptions import DumbProgrammerException


class ItemException(Exception):
    def __init__(self, item_cls: object, msg: str = "", *args: object) -> None:
        if type(item_cls) == type:
            self.item_name = item_cls.__name__
        else:
            self.item_name = item_cls.__class__.__name__
        self.msg = msg
        super().__init__(*args)

    def __str__(self) -> str:
        if not self.msg:
            s = f"An unexpected error has occured with this {self.item_name} item"
        else:
            s = f"{self.item_name}: {self.msg}"
        return s


class ItemIncompleteException(ItemException):
    def __init__(
        self, item_cls: object, required: list[Union[str, Sequence]], *args: object
    ) -> None:
        values_required = required.copy()
        for i, req in enumerate(values_required):
            if type(req) == str:
                values_required[i] = f"'{req}'"
            elif isinstance(req, Sequence):
                values_required[i] = "(" + " AND ".join([f"'{s}'" for s in req]) + ")"
        msg = f"Item must have {' OR '.join(values_required)}"
        super().__init__(item_cls, msg, *args)


class ItemMaxCharError(ItemException):
    def __init__(
        self,
        item_cls: object,
        invalid_attr: str,
        invalid_str: str,
        str_max: int,
        *args: object,
    ) -> None:
        msg = f"{invalid_attr} value of '{invalid_str}' breaks max char limit of {str_max}"
        super().__init__(item_cls, msg, *args)


class ItemInvalidAxlData(ItemException):
    def __init__(self, item_cls: object, failed_item: str = "", *args: object) -> None:
        msg = f"Failed to parse AXL data"
        if failed_item:
            msg += f", could not find {self.failed} in given data"
        super().__init__(item_cls, msg, *args)


@dataclass(frozen=True)
class _ImportItem:
    item_constructor: Callable
    item_map: dict


@dataclass
class _AXLItem:
    axl_data: InitVar[dict] = field(default=None, kw_only=True)
    data_map: ClassVar[dict] = None
    char_limit_map: ClassVar[dict] = None
    required_values: ClassVar[list] = None

    def __post_init__(self, axl_data):
        if axl_data is not None and self.data_map is not None:
            self.__parse_axl_data(axl_data)
        if self.required_values is not None:
            self.__check_required()
        if self.char_limit_map is not None:
            self.__check_char_limits()

    def __parse_axl_data(self, axl_data) -> None:
        for dest, src in self.data_map.items():
            if getattr(self, dest, Empty) is Empty:
                raise DumbProgrammerException(
                    f"data_map dest '{dest}' not an attribute of {self.__class__.__name__} item!"
                )
            if type(src) == _ImportItem:
                import_data = {}
                for idest, isrc in src.item_map.item():
                    import_data[idest] = axl_data.get(isrc, "")
                    axl_data.pop(isrc, None)
                try:
                    new_item = src.item_constructor(**import_data)
                except ItemIncompleteException:
                    raise ItemException(
                        self,
                        f"Failed to import AXL data due to invalid {src.__name__} data",
                    )
                setattr(self, dest, new_item)
            elif (data := axl_data.get(src, Empty)) is Empty:
                raise ItemInvalidAxlData(self, src)
            else:
                setattr(self, dest, data)
                axl_data.pop(src, None)

    def __check_required(self) -> None:
        for req in self.required_values:
            if type(req) == "str":
                if getattr(self, req, "") != "":
                    return None
            elif isinstance(req, Sequence):
                if all(getattr(self, val, None) for val in req):
                    return None
            else:
                raise DumbProgrammerException(
                    "A type beside str or Sequence was used in a 'required check'!"
                )
        else:
            raise ItemIncompleteException(self, self.required_values)

    def __check_char_limits(self) -> None:
        for attr, limit in self.char_limit_map.items():
            if (value := getattr(self, attr, None)) is None:
                raise DumbProgrammerException(
                    f"char_limit_map attr '{attr}' does not exist in {self.__class__.__name__} item!"
                )
            if len(value) > limit:
                raise ItemMaxCharError(self, attr, value, limit)

    def add_axl_data(self, data: dict) -> None:
        if self.data_map is None:
            raise ItemException(self, "AXL data cannot be added to this type of item")
        self.__parse_axl_data(data)

    def to_axl(self) -> dict:
        pass


@dataclass
class _UUIDItem(_AXLItem):
    uuid: str = field(default="", kw_only=True)


@dataclass
class CallingSearchSpace(_UUIDItem):
    name: str = ""
    description: str = ""
    members: list[str] = field(default_factory=list)

    required_values: ClassVar[list] = ["name", "uuid"]
    data_map: ClassVar[dict] = {
        "name": "name",
        "description": "description",
        "members": "members",
    }
    char_limit_map: ClassVar[dict] = {
        "name": 512,
        "description": 1024,
    }

    def __post_init__(self, axl_data):
        super().__post_init__(axl_data)
        for i, css in enumerate(self.members):
            self.members[i] = {"index": i + 1, "routePartitionName": css}


@dataclass
class DirectoryNumber(_UUIDItem):
    pattern: str = ""
    route_partition: str = ""
    description: str = field(default="", kw_only=True)
    alerting_name: str = field(default="", kw_only=True)
    ascii_alerting_name: str = field(default="", kw_only=True)
    vm_profile: str = field(default="", kw_only=True)
    css: CallingSearchSpace = field(default=None, kw_only=True)
    other_attributes: InitVar[dict] = field(default=None, kw_only=True)

    required_values: ClassVar[list] = [("pattern", "route_partition"), "uuid"]
    data_map: ClassVar[dict] = {
        "pattern": "pattern",
        "route_partition": "routePartitionName",
        "description": "description",
        "alerting_name": "alertingName",
        "ascii_alerting_name": "asciiAlertingName",
        "vm_profile": "voiceMailProfileName",
        "css": _ImportItem(CallingSearchSpace, {"name": "shareLineAppearanceCssName"}),
    }
    char_limit_map: ClassVar[dict] = {
        "pattern": 255,
        "route_partition": 50,
        "description": 200,
        "alerting_name": 50,
        "ascii_alerting_name": 30,
        "vm_profile": 50,
    }

    def __post_init__(self, axl_data, other_attributes):
        if type(other_attributes) == dict:
            for attr, value in other_attributes.items():
                setattr(self, attr, value)
        return super().__post_init__(axl_data)
