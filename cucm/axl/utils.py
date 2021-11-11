import inspect
from typing import Callable
from termcolor import colored


def print_signature(func: Callable, parent_class="") -> None:
    """Prints a "pretty" color-coded version of a given function's signature.

    Parameters
    ----------
    func : Callable
        Any function or method
    parent_class : str, optional
        If you want to include the parent class(es), provide them here (e.g. "pandas.Dataframe" or "sys"). Leave blank if there is no parent class(es).
    """
    if parent_class:
        parent_class += "."

    arg_pairs = []
    for arg_name, arg_param in inspect.signature(func).parameters.items():
        arg_str = colored(arg_name, "red")
        if (type_cast := arg_param.annotation.__name__) != "_empty":
            arg_str += f": {colored(type_cast, 'magenta')}"
        elif (default_value := arg_param.default) != inspect._empty:
            if type(default_value) == str:
                default_color = "green"
                default_value = '"' + default_value + '"'
            else:
                default_color = "yellow"
            arg_str += f"={colored(default_value, default_color)}"
        arg_pairs.append(arg_str)

    signature_str = (
        f"{parent_class}{colored(func.__name__, 'cyan')}({', '.join(arg_pairs)})"
    )
    if len(signature_str) > 150:
        nl = "\n"
        comma_nl = ",\n    "  # ? had to write this due to f-string limitation
        signature_str = f"{nl}{parent_class}{colored(func.__name__, 'cyan')}({nl}    {comma_nl.join(arg_pairs)}{nl})"
    print(signature_str, "\n")
