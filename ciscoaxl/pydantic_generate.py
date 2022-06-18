import re
from ciscoaxl import axl
from ciscoaxl.wsdl import AXLElement, get_tree
from zeep.xsd.elements.indicators import Choice, Sequence
from typing import Any, Dict, List, Optional, Set, Tuple
from collections.abc import Iterable
from dataclasses import dataclass
import keyword
from pathlib import Path


@dataclass
class ModelElement:
    name: str
    orig_elem: AXLElement
    pytypes: List[str]

    def __post_init__(self):
        self.alias: Optional[str] = None
        self.extra: Optional[str] = None
        self.optional: bool = False
        self.multiple: bool = False

        if self.orig_elem.max_results > 1:
            self.multiple = True

        if not self.orig_elem.needed or self.multiple:
            self.optional = True
        elif (
            self.name in ("_value_1", "uuid", "ctiid")
            and not self.orig_elem.parent.needed
        ):
            self.optional = True

        if self.name in keyword.kwlist:
            self.alias = self.name
            self.name = self.name + "_"
        elif self.name.startswith("_"):
            self.alias = self.name
            self.name = self.name[1:]
            self.extra = (
                "\n\t@property\n\t"
                + f"def {self.alias}(self) -> {self.type_string()}:"
                + "\n\t\t"
                + f"return self.{self.name}"
                + "\n"
            )

    def type_string(self) -> str:
        if len(self.pytypes) > 1 and self.optional:
            t_str = f"Union[{', '.join(self.pytypes)}, None]"
        elif len(self.pytypes) > 1:
            t_str = f"Union[{', '.join(self.pytypes)}]"
        elif self.optional:
            t_str = f"Optional[{self.pytypes[0]}]"
        else:
            t_str = self.pytypes[0]

        if self.multiple:
            t_str = f"List[{t_str}]"

        return t_str

    def model_string(self) -> str:
        m_str = f"{self.name}: {self.type_string()}"
        if self.alias is not None:
            m_str += f" = Field(alias='{self.alias}')"

        return m_str


@dataclass
class ModelConstruct:
    name: str
    base_elem: AXLElement
    elements: Dict[str, ModelElement]

    def __post_init__(self):
        self.extras: Dict[str, str] = {}
        for name, elem in self.elements.items():
            if elem.extra is not None:
                self.extras[name] = elem.extra

    def schema_string(self) -> str:
        schema = f"class {self.name}(BaseModel):" + "\n"
        for elem in self.elements.values():
            schema += "\t" + elem.model_string() + "\n"
        schema += "\n".join(self.extras.values())
        return schema


# regex to locate AXL 'get' elements used in axl.py
r_getmethod = re.compile(r"^[^#]+self\.client\.(get\w+)\(")

MODEL_FILE_HEADER = """from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field
from typing import Any, Optional, Union, List, Dict


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


def axl_to_python_types(element: AXLElement) -> List[str]:
    """Analyzes an AXL element to determine the closest relative Python type(s)

    Args:
        element (AXLElement): The element to analyze

    Returns:
        List[str]: A list of names of Python types that can be used for the given element
    """

    def parse_accept_types(a_types: list) -> List[str]:
        """Assumes that a_types has non-zero length"""
        return [t.__name__ for t in a_types if t.__module__ == "builtins"]

    etype = element.type
    py_types = ["Any"]  # fallback value

    # XFkType is common but hard to analyze, let's hardcode it
    if etype.name == "XFkType":
        py_types = ["XFkType"]
    # type has it's own defined XSD element
    elif hasattr(etype, "_element") and etype._element:
        if hasattr(etype._element, "type"):
            if etype._element.type.accepted_types:
                py_types = parse_accept_types(etype._element.type.accepted_types)
            elif (
                hasattr(etype._element, "default_value")
                and etype._element.default_value
            ):
                py_types = [type(etype._element.default_value).__name__]
            else:
                py_types = ["Any"]
        else:
            py_types = ["Any"]  # uncommon object(s)
    # type has a list of accepted Python/SOAP types
    elif (
        hasattr(etype, "accepted_types")
        and etype.accepted_types
        and isinstance(etype.accepted_types, Iterable)
    ):
        py_types = parse_accept_types(etype.accepted_types)
    # element is an 'item', need to refer to its item_class
    elif (
        hasattr(etype, "item_class")
        and etype.item_class
        and etype.item_class.accepted_types
        and isinstance(etype.item_class.accepted_types, Iterable)
    ):
        py_types = parse_accept_types(etype.item_class.accepted_types)

    return py_types


def generate_return_models(z_client, element_name: str) -> Dict[str, ModelConstruct]:
    """Take a 'get'/'list' AXL element and return a Pydantic model description of the element's return object

    Args:
        z_client: A Zeep client connected to a UCM instance
        element_name (str): The name of the 'get/'list' element used

    Returns:
        Dict[str, ModelConstruct]: All models required for the output of the given element
    """
    models: Dict[str, ModelConstruct] = {}

    # recursive parsing of element nodes
    def parse_nodes(e: AXLElement) -> str:
        def deal_with_choice(c: AXLElement) -> dict:
            choice_items = {}
            for choice in c.children:
                if not choice.children:
                    choice_items[choice.name] = ModelElement(
                        choice.name, choice, axl_to_python_types(choice)
                    )
                elif choice.type in (Choice, Sequence):
                    choice_items.update(**deal_with_choice(choice))
                else:
                    choice_items[choice.name] = parse_nodes(choice)
            return choice_items

        model_elements: Dict[str, ModelElement] = {}
        for child in e.children:
            if child.type in (Choice, Sequence):
                model_elements.update(**deal_with_choice(child))
            elif child.children:
                model_elements[child.name] = parse_nodes(child)
            else:
                model_elements[child.name] = ModelElement(
                    child.name, child, axl_to_python_types(child)
                )
        # store any new models needed
        models[e.type.name] = ModelConstruct(e.type.name, e, model_elements)

        # pass back up to parent as model element
        return ModelElement(e.name, e, [e.type.name])

    element = get_tree(z_client, element_name)
    rt = element.get("returnedTags", None)

    if rt is None:
        print(f"Skipping {element.name}...no returnedTags node")
        return ""

    parse_nodes(rt)
    return models


def get_elements_used() -> Set[str]:
    """Scans through axl.py and finds any 'get' elements used (ignores comments)

    Returns:
        Set[str]: Set of the names of all 'get' elements found
    """
    found_elements = set()

    axl_file = Path(__file__).parent / "axl.py"
    with axl_file.open("r") as fptr:
        for line in fptr.readlines():
            result = r_getmethod.search(line)
            if result is not None:
                found_elements.add(result.group(1))

    return found_elements


def generate_py_file(z_client) -> None:
    """Generates a Python file with Pydantic models of all elements used in axl.py

    Args:
        z_client: A Zeep client connected to a UCM instance
    """

    def resolve_collision_rename(
        schema: Dict[str, ModelConstruct],
        element_models: Dict[str, ModelConstruct],
        conflict_name: str,
        schema_model_parent: str,
        element_model_parent: str,
    ):
        schema_pack = (schema, schema_model_parent)
        element_pack = (element_models, element_model_parent)

        for pack in (schema_pack, element_pack):
            model_dict, parent_name = pack
            replacement = parent_name + conflict_name
            for model in model_dict.values():
                for element in model.elements.values():
                    if conflict_name in element.pytypes:
                        element.pytypes.remove(conflict_name)
                        element.pytypes.append(replacement)

    all_models: Dict[str, ModelConstruct] = {}
    root_model_refs: Dict[str, ModelConstruct] = {}

    for element in get_elements_used():
        models = generate_return_models(z_client, element)

        # store root model
        root_model = list(models)[-1]
        root_model_refs[element] = models[root_model]

        # resolve collisions
        collisions = {k: v for k, v in models.copy().items() if k in all_models}

        for coll_name, model in collisions.items():
            colliding_model = all_models.get(coll_name, None)
            if colliding_model is None:
                continue  # collision was fixed by previous iteration
            elif [e.type_string() for e in colliding_model.elements.values()] == [
                e.type_string() for e in model.elements.values()
            ]:
                continue  # model already exists in same format
            else:
                parents: Dict[str, AXLElement] = {
                    "model": model.base_elem.parent,
                    "c_model": colliding_model.base_elem.parent,
                }
                while parents["model"].name == parents["c_model"].name:
                    parents["model"] = parents["model"].parent
                    parents["c_model"] = parents["c_model"].parent
                    if any(p is None for p in parents.values()):
                        raise Exception(
                            f"Cannot resolve collision between {colliding_model.base_elem._parent_chain()}"
                            + f" and {model.base_elem._parent_chain()}."
                        )
                # change out the keys IN PLACE to keep order created by previous recursion
                models[coll_name].name = parents["model"].name + coll_name
                models = {
                    k if k != coll_name else parents["model"].name + coll_name: v
                    for k, v in models.items()
                }
                all_models[coll_name].name = parents["c_model"].name + coll_name
                all_models = {
                    k if k != coll_name else parents["c_model"].name + coll_name: v
                    for k, v in all_models.items()
                }
                # fix all nested elements with old names now removed
                resolve_collision_rename(
                    all_models,
                    models,
                    coll_name,
                    parents["c_model"].name,
                    parents["model"].name,
                )

        all_models.update(models)

    savefile = Path(__file__).parent / "pydantic_models.py"
    if savefile.exists():
        savefile.unlink()

    with savefile.open("w") as fptr:
        fptr.write(MODEL_FILE_HEADER)
        fptr.write(
            "\n\n".join(schema.schema_string() for schema in all_models.values())
        )
        fptr.write("\n\nelement_return_class: dict = {\n")
        for elem_name, model in root_model_refs.items():
            fptr.write("\t" + f"'{elem_name}': {model.name}," + "\n")
        fptr.write("}")


if __name__ == "__main__":
    ucm = axl()  # SUPPLY CREDS HERE BUT DON'T COMMIT
    generate_py_file(ucm._zeep)
