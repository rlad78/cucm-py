from zeep import Client
from cucmtoolkit.ciscoaxl.exceptions import WSDLException


def _get_element_child_tree(
    z_client: Client, element_name: str, child_name: str
) -> list[str]:
    # get element
    try:
        element = z_client.get_element(f"ns0:{element_name}")
    except LookupError:
        raise WSDLException(f"Could not find element {element_name}")

    # get type
    try:
        elem_type = z_client.get_type(f"ns0:{element.type.name}")
    except AttributeError:
        raise WSDLException(f"{element_name} has no retrievable type value")
    except LookupError:
        raise WSDLException(f"Could not find type {element.type.name}")

    # find searchCriteria
    for child in elem_type.elements:
        if child[0] == child_name:
            criteria = child[1]  # * [0] is the name, [1] is the obj
            break
    else:
        raise WSDLException(f"Could not find {child_name} for type {elem_type.name}")

    # extract searchCriteria
    return [e[0] for e in criteria.type.elements]


def get_search_criteria(z_client: Client, element_name: str) -> list[str]:
    return _get_element_child_tree(z_client, element_name, child_name="searchCriteria")


def get_return_tags(z_client: Client, element_name: str) -> list[str]:
    return _get_element_child_tree(z_client, element_name, child_name="returnedTags")
