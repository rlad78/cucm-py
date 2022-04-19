from dataclasses import dataclass, field, InitVar
from typing import Union

class ItemIncompleteException(Exception):
    def __init__(self, item_name: str, required: list[Union[str, tuple]], *args: object) -> None:
        self.required = required
        self.item = item_name
        super().__init__(*args)

    def __str__(self) -> str:
        for i, req in enumerate(self.required):
            if type(req) == tuple:
                self.required[i] = "(" + " AND ".join([f"'{s}'" for s in req]) + ")"
            elif type(req) == str:
                self.required[i] = f"'{req}'"
        return f"{self.item} must have {' OR '.join(self.required)}"

class ItemMaxCharError(Exception):
    def __init__(self, s: str, maximum: int, *args: object) -> None:
        self.string = s
        self.maximum = maximum
        super().__init__(*args)

    def __str__(self) -> str:
        return f"'{self.string}' breaks max char limit of {self.maximum}"

def maxchar(s: str, maximum: int) -> None:
    if len(s) > maximum:
        raise ItemMaxCharError(s, maximum)

@dataclass
class UUIDItem:
    uuid: str = ""
    axl_data: InitVar[dict] = {}

@dataclass
class CallingSearchSpace(UUIDItem):
    name: str = ""
    description: str = ""
    members: list[str] = field(default_factory=list)

    def __post_init__(self, axl_data):
        if axl_data:
            pass  # todo: fill out data parse
        elif not self.uuid and not self.name:
            raise ItemIncompleteException("CallSearchSpace", ["uuid", "name"])
        else:
            maxchar(self.name, 512)
            maxchar(self.description, 1024)
            for i, css in enumerate(self.members):
                self.members[i] = {"index": i+1, 'routePartitionName': css}


@dataclass
class DN(UUIDItem):
    pattern: str = ""
    route_parition: str = ""
    description: str = ""
    alerting_name: str = ""
    ascii_alerting_name: str = ""
    vm_profile: str = ""
    css: str = ""
