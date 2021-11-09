from typing import Union
from zeep import Client
from zeep.xsd.elements.element import Element
from zeep.xsd.elements.indicators import Choice, Sequence
from zeep.xsd import Nil
from cucm.axl.exceptions import (
    WSDLDrillDownException,
    WSDLException,
    WSDLInvalidArgument,
    WSDLMissingArguments,
    WSDLChoiceException,
    DumbProgrammerException,
    TagNotValid,
    WSDLValueOnlyException,
    _list_options,
)
from termcolor import colored


class AXLElement:
    def __init__(self, element: Union[Element, Choice], parent=None) -> None:
        self.elem = element
        self.parent = parent

        if type(element) == Sequence:
            self.name = "[ group ]"
            self.type = Sequence
            self.children = [AXLElement(e[1], parent=self) for e in element.elements]
            if self.parent is not None and self.parent.type == Choice:
                self.needed = False
            else:
                self.needed = not element.is_optional
        elif type(element) == Choice:
            self.name = "[ choice ]"
            self.type = Choice
            self.children = [
                AXLElement(e[1], parent=self) for e in element.elements_nested
            ]
            self.child_names = [e.name for e in self.children]
            self.needed = bool(self.elem.min_occurs != 0)
        elif type(element) == Element:
            self.name = element.name
            self.type = element.type
            if self.parent is not None and self.parent.type == Choice:
                self.needed = False
            else:
                self.needed = not element.is_optional
            if hasattr(element.type, "elements"):
                package = element.type.elements_nested[0][1]
                if type(package) == Sequence:
                    self.children = [
                        AXLElement(e, self)
                        for e in package
                        if getattr(e, "name", None) not in ("_value_1", None)
                        or type(e) == Choice
                    ]
                elif type(package) == Element:
                    self.children = (
                        [AXLElement(package, self)]
                        if not package.name.startswith("_value_")
                        else []
                    )
                elif type(package) == Choice:
                    self.children = [AXLElement(package, self)]
                else:
                    raise WSDLException(f"Unknown package format '{type(package)}'")
            else:
                self.children = []
        else:
            raise WSDLException(f"Unknown element format '{type(element)}'")

    def __repr__(self) -> str:
        name = self.name
        xsd_type = type(self.type).__name__
        children = len(self.children)
        return f"AXLElement({name=}, {xsd_type=}, {children=})"

    def _parent_chain(self) -> str:
        if self.parent is None:
            return self.name
        elif self.type == Choice or self.type == Sequence:
            return self.parent._parent_chain()
        else:
            return self.parent._parent_chain() + "." + self.name

    def _parent_chain_required(self) -> bool:
        if self.parent is None and self.needed:
            return True
        elif self.needed:
            return self.parent._parent_chain_required()
        else:
            return False

    def children_dict(self, required=False) -> dict:
        c_dict = dict()
        for child in self.children:
            if child.type == Choice or child.type == Sequence:
                i = 1
                base_name = child.name.split(" ")[1]
                while f"[ {base_name}{i} ]" in c_dict:
                    i += 1
                c_dict[f"[ {base_name}{i} ]"] = child.children_dict(required)
            elif child.children:
                c_dict[child.name] = child.children_dict(required)
            else:
                if required and child.needed:
                    c_dict[child.name] = "(required)"
                else:
                    c_dict[child.name] = ""
        return c_dict

    def print_tree(self, indent=0, show_types=False, show_required=False) -> None:
        branch_str = f"{'  ' * indent if indent < 2 else ('  |' * (indent - 1)) + '  '}{'┗ ' if indent else ''}"
        name_str = self.name
        atrib_str = ""

        if show_required and self.needed and self.parent is not None:
            # branch_str = colored(branch_str, "blue")
            if self._parent_chain_required():
                name_str = colored(self.name, "cyan")
                atrib_str += colored(" (required)", "blue")
            else:
                atrib_str += colored(" (required if parent is used)", "yellow")
        if (
            show_types
            and self.type not in (Sequence, Choice)
            and self.parent is not None
        ):
            atrib_str += colored(f" ({type(self.type).__name__})", "green")

        # print(
        #     f"{'  ' * indent if indent < 2 else ('  |' * (indent - 1)) + '  '}{'┗ ' if indent else ''}",
        #     colored(self.name, "blue")
        #     if (show_required and self.needed and self.parent is not None)
        #     else self.name,
        #     f"({self.type})" if show_types else "",
        #     colored(f"{' (required)' if self.needed else ''}", "blue")
        #     if (show_required and self.parent is not None)
        #     else "",
        #     sep="",
        # )

        print(branch_str, name_str, atrib_str, sep="")

        for child in self.children:
            child.print_tree(indent + 1, show_types, show_required)

    def get(self, name: str):
        if not name:
            return None
        for child in self.children:
            if getattr(child, "name", None) == name:
                return child
        else:
            return None

    def find(self, name: str):
        if not name:
            return None

        for child in self.children:
            if getattr(child, "name", None) == name:
                return child
            elif child.children:
                if (result := child.find(name)) is not None:
                    return result
        else:
            return None

    def validate(self, **kwargs) -> None:
        def choice_peek(choice: AXLElement) -> list[str]:
            peek_names: list[str] = []
            for child in choice.children:
                if not child.name.startswith("[ group"):
                    peek_names.append(child.name)
                elif child.type == Sequence:
                    peek_names.extend([c.name for c in child.children])
            return peek_names

        c_dict = self.children_dict(required=True)
        if self.type == Choice:
            groups: list[list[str]] = [
                [e.name for e in c.children]
                for c in self.children
                if c.name == "[ group ]"
            ]
            elements: list[str] = [
                c.name for c in self.children if not c.name.startswith("[ ")
            ]
            g_found = [g for g in groups if any(e in kwargs for e in g)]
            e_found = [e for e in elements if e in kwargs]
            if len(g_found) + len(e_found) != 1:
                raise WSDLChoiceException(
                    arguments=elements + groups,
                    element_name=self.parent._parent_chain(),
                )
        else:
            for name, value in kwargs.items():
                if type(value) not in (dict, list):  # * normal tag-value pairs
                    if name not in c_dict:
                        choice_list = [
                            c for c in self.children if c.name == "[ choice ]"
                        ]
                        if not choice_list:
                            raise WSDLInvalidArgument(name, self._parent_chain())
                        for choice in choice_list:
                            if name in choice_peek(choice):
                                choice.validate(**kwargs)
                                break
                        else:
                            raise WSDLInvalidArgument(name, self._parent_chain())
                    elif type(c_dict[name]) == dict:
                        raise WSDLDrillDownException(
                            name, c_dict[name], self._parent_chain()
                        )
                elif type(value) == dict:  # * complex types
                    if name not in c_dict:
                        choice_list = [
                            c for c in self.children if c.name == "[ choice ]"
                        ]
                        if not choice_list:
                            raise WSDLInvalidArgument(name, self._parent_chain())
                        for choice in choice_list:
                            if name in choice.children_dict():
                                [c for c in choice.children if c.name == name][
                                    0
                                ].validate(**value)
                                break
                        else:
                            raise WSDLInvalidArgument(name, self._parent_chain())
                    elif type(c_dict[name]) != dict:
                        raise WSDLValueOnlyException(name, self._parent_chain())
                    else:
                        [c for c in self.children if c.name == name][0].validate(
                            **value
                        )
                elif type(value) == list:  # * list of elements
                    if not value or type(value) == str:
                        self.validate(**{name: ""})
                    elif type(value[0]) == dict:
                        for entry in value:
                            self.validate(**{name: entry})
                    else:
                        raise DumbProgrammerException(
                            f"Didn't account for type {type(value)} for value type."
                        )

    def return_tags(self) -> dict:
        if self.parent is not None:
            return self.parent.return_tags()
        elif (tags_element := self.get("returnedTags")) is None:
            return {}

        def get_element_tree(element):
            if not element.children:
                return Nil
            elif element.type == Choice:
                return get_element_tree(element.children[0])
            else:
                # return {e.name: get_element_tree(e) for e in element.children}
                tree_dict: dict = dict()
                for e in element.children:
                    if e.type == Choice:
                        tree_dict[e.children[0].name] = get_element_tree(e.children[0])
                    else:
                        tree_dict[e.name] = get_element_tree(e)
                return tree_dict

        return get_element_tree(tags_element)

    def needed_only(self) -> Union["AXLElement", None]:
        if self.parent is None:
            needed_root = AXLElement(self.elem)
            needed_root.children[:] = [c for c in needed_root.children if c.needed]
            for child in needed_root.children:
                child.needed_only()
            return needed_root
        elif self.type == Choice:
            for child in self.children:
                child.needed_only()
        else:
            self.children[:] = [c for c in self.children if c.needed]
            for child in self.children:
                child.needed_only()


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

    if not hasattr(element.type, "elements") or (
        element.max_occurs != 1 and element.max_occurs != "unbounded"
    ):
        return Nil

    elem_tree: dict = dict()
    for e_name, e_obj in element.type.elements_nested():
        if e_name == "_value_1":
            continue
        elem_tree[e_name] = _get_element_tree(z_client, element=e_obj)
    return elem_tree if len(elem_tree) > 0 else ""


def get_search_criteria(z_client: Client, element_name: str) -> list[str]:
    return __get_element_child_args(z_client, element_name, child_name="searchCriteria")


def get_return_tags(z_client: Client, element_name: str) -> list[str]:
    return __get_element_child_args(z_client, element_name, child_name="returnedTags")


def get_return_tree(z_client: Client, element_name: str) -> dict:
    root_element = __get_element_by_name(z_client, element_name)
    if not hasattr(root_element.type, "elements"):
        return {"error": f"no sub-elements for '{element_name}'"}

    return AXLElement(root_element).return_tags()


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

    # tree = _get_element_tree(z_client, element_name=element_name)
    # if type(tree) == str:
    #     raise WSDLException(
    #         f"Making element tree for '{element_name}' reuslted in nothing"
    #     )

    tags_tree = AXLElement(__get_element_by_name(z_client, element_name)).return_tags()
    if not tags_tree:
        raise WSDLException(f"Element '{element_name}' has no returnedTags sub-element")

    for tag in tags:
        if tags_tree.get(tag, None) != Nil:  # complex tag, replace tag list with dict
            return [tags_in_tree(tags_tree, tags)]
    else:
        return tags


def validate_soap_arguments(z_client: Client, element_name: str, **kwargs) -> bool:
    elem = __get_element_by_name(z_client, element_name)
    tree = AXLElement(elem)
    tree_required = tree.needed_only()


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
