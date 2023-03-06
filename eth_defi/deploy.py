"""Deploy any precompiled contract.

`See Github for available contracts <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/eth_defi/abi>`_.
"""

from typing import Union, TypeAlias, Dict

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_contract


#: Manage internal registry of deployed contracts
#:
#:
ContractRegistry: TypeAlias = Dict[HexAddress, Contract]


def deploy_contract(
        web3: Web3,
        contract: Union[str, Contract],
        deployer: str,
        *constructor_args,
        register_for_tracing=True,
) -> Contract:
    """Deploys a new contract from ABI file.

    A generic helper function to deploy any contract.

    Example:

    .. code-block:: python

        token = deploy_contract(web3, deployer, "ERC20Mock.json", name, symbol, supply)
        print(f"Deployed ERC-20 token at {token.address}")

    :param web3:
        Web3 instance

    :param contract:
        Contract file path as string or contract proxy class

    :param deployer:
        Deployer account

    :param constructor_args:
        Other arguments to pass to the contract's constructor

    :param register_for_tracing:
        Make the symbolic contract information available on web3 instance.

        See :py:func:`get_contract_registry`

    :return:
        Contract proxy instance

    """
    if isinstance(contract, str):
        Contract = get_contract(web3, contract)

        # Used in trace.py
        contract_name = contract.replace(".json", "")

    else:
        Contract = contract
        contract_name = None

    tx_hash = Contract.constructor(*constructor_args).transact({"from": deployer})

    tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

    instance = Contract(
        address=tx_receipt.contractAddress,
    )

    if register_for_tracing:
        instance.name = contract_name
        register_contract(web3, tx_receipt.contractAddress, instance)

    return instance


def get_or_create_contract_registry(web3: Web3) -> ContractRegistry:
    """Get a contract registry associated with a Web3 connection.

    - Only relevant for test sessions

    - Assumes one web3 instance per test

    - Useful to make traces symbolic in :py:mod:`eth_defi.trace`

    :param web3:
        Web3 test session

    :return:
        Mapping of address -> deployed contract instance
    """
    if not hasattr(web3, "contract_registry"):
        web3.contract_registry = {}

    return web3.contract_registry


def register_contract(web3, address: HexAddress, instance: Contract):
    """Register a contract for tracing."""

    registry = get_or_create_contract_registry(web3)
    registry[address] = instance
