from functools import wraps
from typing import Callable, TypeVar, Union
import inspect
from zeep.helpers import serialize_object
from copy import deepcopy
from cucm.axl.exceptions import *
from cucm.axl.wsdl import (
    get_return_tags,
    fix_return_tags,
    validate_arguments,
)
from cucm.utils import Empty
import cucm.axl.configs as cfg
import asyncio


TCallable = TypeVar("TCallable", bound=Callable)

def _tag_serialize_filter(tags: Union[list, dict], data: dict) -> dict:
    def check_value(d: dict) -> dict:
        d_copy = d.copy()
        for tag, value in d_copy.items():
            if type(value) == dict:
                if "_value_1" in value:
                    d_copy[tag] = value["_value_1"]
                else:
                    d_copy[tag] = check_value(value)
            elif type(value) == list:
                for i, d in enumerate(deepcopy(value)):
                    if type(d) == dict:
                        value[i] = check_value(d)
        return d_copy

    if tags is None:
        return check_value(data)

    working_data = deepcopy(data)
    for tag, value in data.items():
        if tag not in tags and len(tags) > 0:
            working_data.pop(tag, None)
        elif type(value) == dict:
            if "_value_1" in value:
                working_data[tag] = value["_value_1"]
            else:
                working_data[tag] = check_value(value)
        elif type(value) == list:
            for i, d in enumerate(deepcopy(value)):
                if type(d) == dict:
                    value[i] = check_value(d)
    return working_data


# def serialize(func: TCallable) -> TCallable:
#     @wraps(func)
#     def wrapper(*args, **kwargs):
#         r_value = func(*args, **kwargs)
#         if cfg.DISABLE_SERIALIZER:
#             return r_value

#         if r_value is None:
#             return dict()
#         elif issubclass(type(r_value), Fault):
#             raise AXLFault(r_value)
#         elif (
#             "return_tags" not in kwargs
#             and (
#                 tags_param := inspect.signature(func).parameters.get(
#                     "return_tags", None
#                 )
#             )
#             is not None
#         ):
#             r_dict = serialize_object(r_value, dict)
#             return _tag_serialize_filter(tags_param.default, r_dict)
#         elif "return_tags" in kwargs:
#             r_dict = serialize_object(r_value, dict)
#             return _tag_serialize_filter(kwargs["return_tags"], r_dict)
#         else:
#             return serialize_object(r_value, dict)

#     return wrapper


def serialize(func: TCallable) -> TCallable:
    def processing(r_value, f_kwargs):
        if r_value is None:
            return dict()
        elif issubclass(type(r_value), Fault):
            raise AXLFault(r_value)
        elif (
            "return_tags" not in f_kwargs
            and (
                tags_param := inspect.signature(func).parameters.get(
                    "return_tags", Empty
                )
            )
            is not Empty
        ):
            r_dict = serialize_object(r_value, dict)
            return _tag_serialize_filter(tags_param.default, r_dict)
        elif "return_tags" in f_kwargs:
            r_dict = serialize_object(r_value, dict)
            return _tag_serialize_filter(f_kwargs["return_tags"], r_dict)
        else:
            r_dict = serialize_object(r_value, dict)
            return _tag_serialize_filter(None, r_dict)

    def processing_list(r_value, f_kwargs):
        if (
            "return_tags" not in f_kwargs
            and (
                tags_param := inspect.signature(func).parameters.get(
                    "return_tags", Empty
                )
            )
            is not Empty
        ):
            return [
                _tag_serialize_filter(
                    tags_param.default, serialize_object(element, dict)
                )
                for element in r_value
            ]
        elif "return_tags" in f_kwargs:
            return [
                _tag_serialize_filter(
                    f_kwargs["return_tags"], serialize_object(element, dict)
                )
                for element in r_value
            ]
        else:
            return [_tag_serialize_filter(None, serialize_object(element, dict)) for element in r_value]
             
    @wraps(func)
    def wrapper(*args, **kwargs):
        r_value = func(*args, **kwargs)
        if cfg.DISABLE_SERIALIZER:
            return r_value
        if type(r_value) == list:
            return processing_list(r_value, kwargs)
        else:
            return processing(r_value, kwargs)

    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        r_value = await func(*args, **kwargs)
        if cfg.DISABLE_SERIALIZER:
            return r_value
        if type(r_value) == list:
            return processing_list(r_value, kwargs)
        else:
            return processing(r_value, kwargs)

    if inspect.iscoroutinefunction(func):
        return async_wrapper
    else:
        return wrapper


# def serialize_list(func: TCallable) -> TCallable:
#     @wraps(func)
#     def wrapper(*args, **kwargs):
#         r_value = func(*args, **kwargs)
#         if cfg.DISABLE_SERIALIZER:
#             return r_value

#         if type(r_value) != list:
#             return r_value
#         elif (
#             "return_tags" not in kwargs
#             and (
#                 tags_param := inspect.signature(func).parameters.get(
#                     "return_tags", None
#                 )
#             )
#             is not None
#         ):
#             return [
#                 _tag_serialize_filter(
#                     tags_param.default, serialize_object(element, dict)
#                 )
#                 for element in r_value
#             ]
#         elif "return_tags" in kwargs:
#             return [
#                 _tag_serialize_filter(
#                     kwargs["return_tags"], serialize_object(element, dict)
#                 )
#                 for element in r_value
#             ]

#     return wrapper


def check_tags(element_name: str):
    def check_tags_decorator(func: TCallable) -> TCallable:
        def processing(func, args, kwargs):
            if (
                tags_param := inspect.signature(func).parameters.get(
                    "return_tags", Empty
                )
            ) is Empty:
                raise DumbProgrammerException(
                    f"No 'return_tags' param on {func.__name__}()"
                )
            elif tags_param.kind != tags_param.KEYWORD_ONLY:
                raise DumbProgrammerException(
                    f"Forgot to add '*' before return_tags on {func.__name__}()"
                )
            elif not element_name:
                raise DumbProgrammerException(
                    f"Forgot to provide element_name in check_tags decorator on {func.__name__}!!!"
                )
            elif "return_tags" not in kwargs:
                # tags are default
                # if len(tags_param.default) == 0:
                if tags_param.default is None:
                    kwargs["return_tags"] = fix_return_tags(
                        z_client=args[0].zeep,
                        element_name=element_name,
                        tags=get_return_tags(args[0].zeep, element_name),
                    )
            elif type(kwargs["return_tags"]) == list:
                # supply all legal tags if an empty list is provided
                if len(kwargs["return_tags"]) == 0:
                    kwargs["return_tags"] = fix_return_tags(
                        z_client=args[0].zeep,
                        element_name=element_name,
                        tags=get_return_tags(args[0].zeep, element_name),
                    )
                else:
                    kwargs["return_tags"] = fix_return_tags(
                        z_client=args[0].zeep,
                        element_name=element_name,
                        tags=kwargs["return_tags"],
                    )
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            if cfg.DISABLE_CHECK_ARGS:
                return await func(*args, **kwargs)
            processing(func, args, kwargs)
            return await func(*args, **kwargs)
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            if cfg.DISABLE_CHECK_TAGS:
                return func(*args, **kwargs)
            processing(func, args, kwargs)
            return func(*args, **kwargs)

        if inspect.iscoroutinefunction(func):
            async_wrapper.element_name = element_name
            async_wrapper.check = "tags"
            return async_wrapper
        else:
            wrapper.element_name = element_name
            wrapper.check = "tags"
            return wrapper

    return check_tags_decorator


def operation_tag(element_name: str):
    def operation_tag_decorator(func: TCallable) -> TCallable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        wrapper.element_name = element_name
        return wrapper

    return operation_tag_decorator


def check_arguments(element_name: str, child=None):
    def check_argument_deorator(func: TCallable) -> TCallable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if cfg.DISABLE_CHECK_ARGS:
                return func(*args, **kwargs)

            # get non-default kwargs
            default_kwargs = list(inspect.signature(func).parameters)
            user_kwargs = {k: v for k, v in kwargs.items() if k not in default_kwargs}
            validate_arguments(args[0].zeep, element_name, child=child, **user_kwargs)
            return func(*args, **kwargs)

        wrapper.element_name = element_name
        wrapper.check = "args"
        return wrapper

    return check_argument_deorator
