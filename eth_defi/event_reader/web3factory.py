"""Web3 connection factory.

Methods for creating Web3 connections over multiple threads and processes.
"""
from threading import local
from typing import Protocol, Optional, Any

import requests
from requests.adapters import HTTPAdapter
from web3 import HTTPProvider, Web3

from eth_defi.chain import install_chain_middleware, install_retry_middleware
from eth_defi.event_reader.fast_json_rpc import patch_web3


_web3_thread_local_cache = local()


class Web3Factory(Protocol):
    """Create a new Web3 connection.

    - Web3 connection cannot be passed across thread/process boundaries

    - Help to setup TCP/IP connections and Web3 instance over it in threads and processes

    - When each worker is initialised, the factory is called to get JSON-RPC connection

    `See Python documentation regarding typing.Protocol <https://stackoverflow.com/questions/68472236/type-hint-for-callable-that-takes-kwargs>`__.
    """

    def __call__(self, context: Optional[Any] = None) -> Web3:
        """Create a new Web3 connection.

        :param context:
            Any context arguments a special factory might need.

        :return:
            New Web3 connection
        """


class TunedWeb3Factory(Web3Factory):
    """Create a Web3 connections.

    A factory that allows us to pass web3 connection creation method
    across thread and process bounderies.

    - Disable AttributedDict middleware and other middleware that slows us down

    - Enable graceful retries in the case of network errors and API throttling

    - Use faster `ujson` instead of stdlib json to decode the responses
    """

    def __init__(self,
                 json_rpc_url: str,
                 http_adapter: Optional[HTTPAdapter] = None,
                 thread_local_cache=False,
                 ):
        """Set up a factory.

        :param json_rpc_url:
            Node JSON-RPC server URL.

        :param http_adapter:
            Connection pooling for HTTPS.

            Parameters for `requests` library.
            Default to pool size 10.

        :param thread_local:
            Construct the web3 connection only once per thread.

        """
        self.json_rpc_url = json_rpc_url

        if not http_adapter:
            http_adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)

        self.http_adapter = http_adapter
        self.thread_local_cache = thread_local_cache

    def __call__(self, context: Optional[Any] = None) -> Web3:
        """Create a new Web3 connection.

        - Get rid of middleware

        - Patch for ujson
        """

        if self.thread_local_cache:
            web3 = getattr(_web3_thread_local_cache, "web3", None)
            if web3 is not None:
                return web3

        # Reuse HTTPS session for HTTP 1.1 keep-alive
        session = requests.Session()
        session.mount("https://", self.http_adapter)

        web3 = Web3(HTTPProvider(self.json_rpc_url, session=session))

        # Enable faster ujson reads
        patch_web3(web3)

        web3.middleware_onion.clear()
        install_chain_middleware(web3)
        install_retry_middleware(web3)

        if self.thread_local_cache:
            _web3_thread_local_cache.web3 = web3

        return web3


class SimpleWeb3Factory:
    """Single reusable Web3 connection.

    - Does not work for multithreaded use cases, because Web3 object
      with TCP/IP connection is not passable across thread or process boundaries

    - Useful for testing
    """

    def __init__(self, web3: Web3):
        self.web3 = web3

    def __call__(self, context: Optional[Any] = None) -> Web3:
        return self.web3
