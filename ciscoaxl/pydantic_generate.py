from ciscoaxl.wsdl import AXLElement, get_tree
from zeep.xsd.elements.indicators import Choice, Sequence
from typing import Dict
from collections import namedtuple
from collections.abc import Iterable

validator_info = namedtuple("validator_info", ["model_name", "validator_type", "items"])


def axl_to_python_type(element: AXLElement) -> str:
    def parse_accept_types(a_types) -> str:
        """Assumes that a_types has non-zero length"""
        py_types = [t.__name__ for t in a_types if t.__module__ == "builtins"]
        if len(py_types) > 1:
            return f"Union[{', '.join(py_types)}]"
        else:
            return py_types[0]

    etype = element.type

    if hasattr(etype, "_element") and etype._element:
        if hasattr(etype._element, "type"):
            if etype._element.type.accepted_types:
                return parse_accept_types(etype._element.type.accepted_types)
            elif (
                hasattr(etype._element, "default_value")
                and etype._element.default_value
            ):
                return type(etype._element.default_value).__name__
            else:
                return "Any"
        else:
            return "Any"  # uncommon object(s)
    elif (
        hasattr(etype, "accepted_types")
        and etype.accepted_types
        and isinstance(etype.accepted_types, Iterable)
    ):
        return parse_accept_types(etype.accepted_types)
    elif (
        hasattr(etype, "item_class")
        and etype.item_class
        and etype.item_class.accepted_types
        and isinstance(etype.item_class.accepted_types, Iterable)
    ):
        return parse_accept_types(etype.item_class.accepted_types)
    else:
        return "str"


def generate_return_models(z_client, element_name: str) -> dict:
    models: Dict[str, dict] = {}

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
        return e.type.name

    element = get_tree(z_client, element_name)
    rt = element.get("returnedTags", None)

    if rt is None:
        print(f"Skipping {element.name}...no returnedTags node")
        return ""

    parse_nodes(rt)

    schemas: Dict[str, str] = {}
    for type_name, items in models.items():
        schema = "\t" + f"class {type_name}(BaseClass):" + "\n"
        for var_name, var_type in items.items():
            schema += "\t\t" + f"{var_name}: {var_type}" + "\n"
        schemas[type_name] = schema

    return schemas
