import pytest
from cucm import get_credentials
from cucm.axl.asyncaxl import AsyncAXL
from cucm.axl.exceptions import *
from cucm.debug import get_url_and_port
import asyncio
import tests.env as env

pytestmark = pytest.mark.asyncio


async def check_tag_setups(
    func: asyncio.coroutine,
    example_tags: list[str],
    uuid=True,
    list_return=False,
    **kwargs
):
    tasks = {}
    tasks["blank_tags"] = asyncio.create_task(func(**kwargs))
    tasks["single_tag"] = asyncio.create_task(
        func(**kwargs, return_tags=[example_tags[0]])
    )
    tasks["multiple_tags"] = asyncio.create_task(
        func(**kwargs, return_tags=example_tags)
    )
    if uuid:
        tasks["uuid_only"] = asyncio.create_task(func(**kwargs, return_tags=["uuid"]))

    def basic_asserts(task: asyncio.Task, tags: list[str] = None):
        if tags is None:
            tags = example_tags

        assert task.exception() is None

        result = task.result()
        if list_return:
            assert type(result) == list
            assert len(result) > 0
            result = result[0]

        assert type(result) == dict
        assert all(t in result for t in tags)
        if uuid:
            assert "uuid" in result

    await tasks["blank_tags"]
    basic_asserts(tasks["blank_tags"])

    await tasks["single_tag"]
    basic_asserts(tasks["single_tag"], [example_tags[0]])

    await tasks["multiple_tags"]
    basic_asserts(tasks["multiple_tags"])

    if uuid:
        await tasks["uuid_only"]
        basic_asserts(tasks["uuid_only"], ["uuid"])


class TestPhone:
    ucm = AsyncAXL(*get_credentials(), *get_url_and_port())

    async def test_get_phone(self):
        # start with simple GET
        result = await self.ucm.get_phone(
            name=env.PHONE_1_NAME, return_tags=["description"]
        )
        assert result["description"] == env.PHONE_1_DESCRIPTION

        await check_tag_setups(
            self.ucm.get_phone,
            ["name", "description", "lines"],
            name=env.PHONE_1_NAME,
        )

        # check non-existing phone returns empty
        result = await self.ucm.get_phone(
            name=env.PHONE_DOESNT_EXIST_NAME, return_tags=["name"]
        )
        assert type(result) == dict
        assert len(result) == 0

        # check empty name raises exception
        with pytest.raises(InvalidArguments):
            result = await self.ucm.get_phone(name="")

    async def test_get_phones(self):
        # simple get
        result = await self.ucm.get_phones(
            names=env.PHONE_LIST_NAMES, return_tags=["description"]
        )
        assert type(result) == list
        assert len(result) == len(env.PHONE_LIST_NAMES)
        assert all(type(e) == dict for e in result)
        assert all(
            p[0]["description"] == p[1]
            for p in zip(result, env.PHONE_LIST_DESCRIPTIONS)
        )

        await check_tag_setups(
            self.ucm.get_phones,
            ["name", "description", "lines"],
            list_return=True,
            names=env.PHONE_LIST_NAMES,
        )

        # check single phone
        result = await self.ucm.get_phones(
            names=[env.PHONE_1_NAME], return_tags=["description"]
        )
        assert type(result) == list
        assert len(result) == 1
        assert type(result[0]) == dict
        assert result[0]["description"] == env.PHONE_1_DESCRIPTION

        # check empty list
        with pytest.raises(InvalidArguments):
            await self.ucm.get_phones(names=[])

        # check no-valid-results returns list of empty dicts
        result = await self.ucm.get_phones(names=env.PHONE_LIST_DOESNT_EXIST)
        assert type(result) == list
        assert len(result) == len(env.PHONE_LIST_DOESNT_EXIST)
        assert all(type(e) == dict for e in result)
        assert all(len(e) == 0 for e in result)
