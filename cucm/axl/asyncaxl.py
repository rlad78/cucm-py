import cucm.axl.configs as cfg
import asyncio
from zeep import AsyncClient, Settings
from zeep.transports import AsyncTransport
import httpx
from pathlib import Path


class AsyncAXL:
    def __init__(self, username: str, password: str, server: str, port: str = "8443") -> None:
        wsdl_path = cfg.ROOT_DIR / "schema" / "11.5" / "AXLAPI.wsdl"
        
        settings = Settings(
            strict=False, xml_huge_tree=True, xsd_ignore_sequence_order=True
        )
        httpx_client = httpx.AsyncClient(auth=(username, password))
        
        self.async_zeep = AsyncClient(str(wsdl_path), settings=settings, transport=AsyncTransport(client=httpx_client))
        self.aclient = self.async_zeep.create_service(
            "{http://www.cisco.com/AXLAPIService/}AXLAPIBinding",
            f"https://{server}:{port}/axl/",
        )

    