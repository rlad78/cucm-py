from ciscoaxl import axl
from ciscoaxl.wsdl import AXLElement, get_tree
from zeep.xsd.elements.indicators import Choice, Sequence
from typing import Any, Dict, List
from collections import namedtuple
from collections.abc import Iterable
import keyword
from pathlib import Path

validator_info = namedtuple("validator_info", ["model_name", "validator_type", "items"])

MODEL_FILE_HEADER = """from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field
from typing import Any, Optional, Union, List


class BaseModel(PydanticBaseModel):
    def __getitem__(self, item):
        try:
            return getattr(self, item)
        except AttributeError as err:
            try:
                return getattr(self, item + "_")
            except AttributeError:
                raise err from None

    class Config:
        allow_population_by_field_name = True
        underscore_attrs_are_private = False


"""


def axl_to_python_type(element: AXLElement) -> str:
    def parse_accept_types(a_types) -> str:
        """Assumes that a_types has non-zero length"""
        py_types = [t.__name__ for t in a_types if t.__module__ == "builtins"]
        if len(py_types) > 1:
            return f"Union[{', '.join(py_types)}]"
        else:
            return py_types[0]

    etype = element.type
    py_type = "Any"

    if etype.name == "XFkType":
        py_type = "XFkType"
    elif hasattr(etype, "_element") and etype._element:
        if hasattr(etype._element, "type"):
            if etype._element.type.accepted_types:
                py_type = parse_accept_types(etype._element.type.accepted_types)
            elif (
                hasattr(etype._element, "default_value")
                and etype._element.default_value
            ):
                py_type = type(etype._element.default_value).__name__
            else:
                py_type = "Any"
        else:
            py_type = "Any"  # uncommon object(s)
    elif (
        hasattr(etype, "accepted_types")
        and etype.accepted_types
        and isinstance(etype.accepted_types, Iterable)
    ):
        py_type = parse_accept_types(etype.accepted_types)
    elif (
        hasattr(etype, "item_class")
        and etype.item_class
        and etype.item_class.accepted_types
        and isinstance(etype.item_class.accepted_types, Iterable)
    ):
        py_type = parse_accept_types(etype.item_class.accepted_types)

    if element.max_results > 1:
        py_type = f"List[{py_type}]"

    if not element.needed:
        if py_type.startswith("Union[") and "None" not in py_type:
            py_type = "Union[None, " + py_type.removeprefix("Union[")
        else:
            py_type = f"Optional[{py_type}]"
    elif element.name in ("_value_1", "uuid", "ctiid") and not element.parent.needed:
        py_type = f"Optional[{py_type}]"
    return py_type


def generate_return_models(z_client, element_name: str) -> Dict[str, str]:
    models: Dict[str, Dict[str, Any]] = {}

    def parse_nodes(e: AXLElement) -> str:
        def deal_with_choice(c: AXLElement) -> dict:
            choice_items = {}
            for choice in c.children:
                if not choice.children:
                    choice_items[choice.name] = axl_to_python_type(choice)
                elif choice.type in (Choice, Sequence):
                    choice_items.update(**deal_with_choice(choice))
                else:
                    choice_items[choice.name] = parse_nodes(choice)
            return choice_items

        items = {}
        for child in e.children:
            if child.type in (Choice, Sequence):
                items.update(**deal_with_choice(child))
            elif child.children:
                items[child.name] = parse_nodes(child)
            else:
                items[child.name] = axl_to_python_type(child)
        models[e.type.name] = items

        item_name = e.type.name
        if e.max_results > 1:
            item_name = f"List[{item_name}]"
        if not e.needed:
            item_name = f"Optional[{item_name}]"

        return item_name

    element = get_tree(z_client, element_name)
    rt = element.get("returnedTags", None)

    if rt is None:
        print(f"Skipping {element.name}...no returnedTags node")
        return ""

    parse_nodes(rt)

    schemas: Dict[str, str] = {}
    for type_name, items in models.items():
        schema = f"class {type_name}(BaseModel):" + "\n"
        extra = ""

        for var_name, var_type in items.items():
            if var_name in keyword.kwlist + dir(__builtins__):
                schema += (
                    "\t" + f"{var_name}_: {var_type} = Field(alias='{var_name}')" + "\n"
                )
            elif var_name.startswith("_"):
                schema += (
                    "\t"
                    + f"{var_name[1:]}: {var_type} = Field(alias='{var_name}')"
                    + "\n"
                )
                extra += (
                    "\n\t@property\n\t"
                    + f"def {var_name}(self) -> {var_type}:"
                    + "\n\t\t"
                    + f"return self.{var_name[1:]}"
                    + "\n"
                )
            else:
                schema += "\t" + f"{var_name}: {var_type}" + "\n"

        schemas[type_name] = schema + extra

    return schemas


def get_elements_used() -> List[str]:
    return ["getPhone"]  # ! just for testing


def generate_py_file(z_client) -> None:
    all_models: Dict[str, str] = {}

    for element in get_elements_used():
        models = generate_return_models(z_client, element)

        # check for collisions
        for model_name, schema in models.items():
            if all_models.get(model_name, None) == schema:
                raise Exception(
                    f"Schema collision for {model_name}:"
                    + "\n\n-----\n\n"
                    + all_models.get(model_name, None)
                    + "\n\n-----\n\n"
                    + schema
                )

        all_models.update(models)

    savefile = Path(__file__).parent / "pydantic_models.py"
    if savefile.exists():
        savefile.unlink()

    with savefile.open("w") as fptr:
        fptr.write(MODEL_FILE_HEADER)
        fptr.write("\n\n".join(schema for schema in all_models.values()))


if __name__ == "__main__":
    ucm = axl()  # SUPPLY CREDS HERE BUT DON'T COMMIT
    generate_py_file(ucm._zeep)
