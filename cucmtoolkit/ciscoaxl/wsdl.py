from typing import Union
from zeep import Client
from zeep.xsd.elements.element import Element
from cucmtoolkit.ciscoaxl.exceptions import (
    WSDLException,
    DumbProgrammerException,
    TagNotValid,
)


class AXLElement:
    def __init__(self, element: Element, parent=None) -> None:
        self._element = element
        self.type = element.type
        self.name = element.name
        self.parent = parent
        self.required = True if element.min_occurs > 0 else False

        # find children
        self.children = []
        if hasattr(element.type, "elements"):
            for child in [e[1] for e in element.type.elements]:
                axl_elem = AXLElement(child, self)
                if axl_elem.name is not None:
                    self.children.append(axl_elem)

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"{self.name}{' (required)' if self.required else ''}"


def __get_element_by_name(z_client: Client, element_name: str) -> Element:
    try:
        element = z_client.get_element(f"ns0:{element_name}")
    except LookupError:
        raise WSDLException(f"Could not find element {element_name}")
    return element


def __get_element_child_args(
    z_client: Client, element_name: str, child_name: str
) -> list[str]:
    # get element
    elem_type = __get_element_by_name(z_client, element_name).type

    # find searchCriteria
    for child in elem_type.elements:
        if child[0] == child_name:
            criteria = child[1]  # * [0] is the name, [1] is the obj
            break
    else:
        raise WSDLException(f"Could not find {child_name} for type {elem_type.name}")

    # extract searchCriteria
    return [e[0] for e in criteria.type.elements]


def _get_element_tree(
    z_client: Client, element=None, element_name=""
) -> Union[dict, str]:
    if element is not None:
        pass
    elif element_name:
        element = __get_element_by_name(z_client, element_name)
    else:
        DumbProgrammerException(
            "Used get_element_tree without passing in an element name or element obj"
        )

    if not hasattr(element.type, "elements") or element.max_occurs != 1:
        return ""  # no needed children

    elem_tree: dict = dict()
    for e_name, e_obj in element.type.elements:
        if e_name == "_value_1":
            continue
        elem_tree[e_name] = _get_element_tree(z_client, element=e_obj)
    return elem_tree if len(elem_tree) > 0 else ""


def get_element_map(
    z_client: Client, element=None, element_name=""
) -> Union[AXLElement, None]:
    if element is not None:
        root_element = AXLElement(element)
    elif element_name:
        root_element = AXLElement(__get_element_by_name(z_client, element_name))
    else:
        DumbProgrammerException(
            "Used get_element_map without passing in an element name or element obj"
        )

    # if root_element


def get_search_criteria(z_client: Client, element_name: str) -> list[str]:
    return __get_element_child_args(z_client, element_name, child_name="searchCriteria")


def get_return_tags(z_client: Client, element_name: str) -> list[str]:
    return __get_element_child_args(z_client, element_name, child_name="returnedTags")


def get_return_tree(z_client: Client, element_name: str) -> dict:
    root_element = __get_element_by_name(z_client, element_name)
    if not hasattr(root_element.type, "elements"):
        return {"error": f"no sub-elements for '{element_name}'"}

    for e_name, e_obj in root_element.type.elements:
        if e_name == "returnedTags":
            return _get_element_tree(z_client, element=e_obj)
    else:
        return {"error": f"no 'returnedTags' element for '{element_name}'"}


def __pick_from_tags_tree(tags: list[str], tree: dict) -> dict:
    picked_tree: dict = dict()
    for tag in tags:
        tag_value = tree.get(tag, None)
        if tag_value is None:
            raise TagNotValid(
                tag,
            )


def fix_return_tags(z_client: Client, element_name: str, tags: list[str]) -> list:
    def tags_in_tree(tree: dict, tags: list[str]) -> dict:
        picked_tree: dict = dict()
        for tag in tags:
            picked_tree[tag] = tree.get(tag, None)
            if picked_tree[tag] is None:
                raise TagNotValid(
                    tag, get_return_tags(z_client, element_name), elem_name=element_name
                )
        return picked_tree

    tree = _get_element_tree(z_client, element_name=element_name)
    if type(tree) == str:
        raise WSDLException(
            f"Making element tree for '{element_name}' reuslted in nothing"
        )

    tags_tree = tree.get("returnedTags", None)
    if tags_tree is None:
        raise WSDLException(f"Element '{element_name}' has no returnedTags sub-element")

    for tag in tags:
        if tags_tree.get(tag, None) != "":  # complex tag, replace tag list with dict
            return [tags_in_tree(tags_tree, tags)]
    else:
        return tags


def print_element_layout(z_client: Client, element_name: str) -> None:
    def print_element(elem: AXLElement, indent=0) -> None:
        if elem.name == "_value_1":
            return None

        print(
            f"{'|  ' * indent}{elem}{' (required)' if elem.required and elem.parent is not None else ''}{':' if elem.children and elem.children[0].name != '_value_1' else ''}"
        )
        for child in elem.children:
            print_element(child, indent + 1)

    root: AXLElement = AXLElement(__get_element_by_name(z_client, element_name))
    for child in root.children:
        print_element(child)
