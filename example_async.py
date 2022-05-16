# %%
from cucm.axl.wsdl import *


# %%
import asyncio
from cucm.axl.asyncaxl import AsyncAXL
from cucm.axl.credentials import get_credentials
from cucm.debug import set_url_and_port, get_url_and_port
import json

# used for ease of testing
username, password = get_credentials()

try:
    server, port = get_url_and_port()
except Exception:
    set_url_and_port()
    server, port = get_url_and_port()

ucm = AsyncAXL(username, password, server, port)
dummy_sql = "select name,tkmodel from TypeProduct"


# %%
async def get_single_phone(name: str) -> dict:
    """Get the details of a single phone with a given `name`"""
    return await ucm.get_phone(
        name=name,
        return_tags=["name", "description", "model", "callingSearchSpaceName"],
    )


async def get_matching_phones(description_contains: str) -> list[dict]:
    """Get all phones with a description that contain the word in `description_contains`
    and return only specific values for each phone
    """
    return await ucm.find_phones(
        desc_search=f"%{description_contains}%",
        return_tags=["name", "description", "callingSearchSpaceName"],
    )


async def get_phone_lines_many(phone_names: Sequence[str]) -> dict[str, list]:
    """Print specific line values for all the phones given in `phone_names`"""
    tasks: dict[asyncio.Task, str] = {}
    for name in phone_names:
        task = asyncio.create_task(
            ucm.get_phone_lines(name, return_tags=["index", "label", "display"])
        )
        tasks[task] = name

    await asyncio.wait(tasks)

    return {name: t.result() for t, name in tasks.items()}


async def main() -> None:
    results = await asyncio.gather(
        get_single_phone("BOTRCARTE4"),
        get_matching_phones("DEMO"),
        get_phone_lines_many(["BOTRCARTE4", "SEP000000001111"]),
    )

    print("\n ======= get_single_phone =======\n")
    print(json.dumps(results[0], indent=2))

    print("\n ======= get_matching_phones =======\n")
    for phone in results[1]:
        print(json.dumps(phone, indent=2))

    print("\n ======= get_phone_lines_many =======\n")
    for phone_name, lines in results[2].items():
        print(f" ------- {phone_name} -------")
        for line in lines:
            print(json.dumps(line, indent=2))
        print()


if __name__ == "__main__":
    asyncio.run(main(), debug=True)
