"""Test transactions using the vault owner interface.

"""
import secrets
from functools import partial
from typing import cast
from decimal import Decimal

import pytest
from eth.constants import ZERO_ADDRESS
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3, HTTPProvider
from web3.contract import Contract
from web3.exceptions import ContractLogicError

from eth_defi.deploy import deploy_contract
from eth_defi.enzyme.deployment import EnzymeDeployment, RateAsset
from eth_defi.enzyme.events import fetch_vault_balance_events, Deposit, Redemption
from eth_defi.enzyme.price_feed import fetch_price_feeds, EnzymePriceFeed
from eth_defi.enzyme.uniswap_v2 import prepare_swap
from eth_defi.enzyme.vault import Vault
from eth_defi.enzyme.vault_controlled_wallet import VaultControlledWallet, EnzymeVaultTransaction, AssetDelta
from eth_defi.event_reader.multithread import MultithreadEventReader
from eth_defi.event_reader.reader import extract_events, Web3EventReader
from eth_defi.hotwallet import HotWallet
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation, TransactionAssertionError, assert_call_success_with_explanation
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment, FOREVER_DEADLINE


@pytest.fixture
def hot_wallet(web3, deployer, user_1, usdc: Contract) -> HotWallet:
    """Create hot wallet for the signing tests.

    Top is up with some gas money and 500 USDC.
    """
    private_key = HexBytes(secrets.token_bytes(32))
    account = Account.from_key(private_key)
    wallet = HotWallet(account)
    wallet.sync_nonce(web3)
    tx_hash = web3.eth.send_transaction({"to": wallet.address, "from": user_1, "value": 15*10**18})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_hash = usdc.functions.transfer(wallet.address, 500*10**6).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # See howt wallet works
    tx_data = usdc.functions.transfer(deployer, 1*10**6).build_transaction({"from": wallet.address, "gas": 100_000})
    signed = wallet.sign_transaction_with_new_nonce(tx_data)
    tx_hash = web3.eth.send_raw_transaction(signed.raw_transaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    return wallet


@pytest.fixture
def deployment(
    web3: Web3,
    deployer: HexAddress,
    hot_wallet: HotWallet,
    weth: Contract,
    mln: Contract,
    usdc: Contract,
    weth_usd_mock_chainlink_aggregator: Contract,
    usdc_usd_mock_chainlink_aggregator: Contract,
) -> EnzymeDeployment:
    """Create Enzyme deployment that supports WETH and USDC tokens"""

    deployment = EnzymeDeployment.deploy_core(
        web3,
        deployer,
        mln,
        weth,
    )

    tx_hash = deployment.add_primitive(
        usdc,
        usdc_usd_mock_chainlink_aggregator,
        RateAsset.USD,
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx_hash = deployment.add_primitive(
        weth,
        weth_usd_mock_chainlink_aggregator,
        RateAsset.USD,
    )
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Set ethUsdAggregator needed for Enzyme's internal functionality
    tx_hash = deployment.contracts.value_interpreter.functions.setEthUsdAggregator(weth_usd_mock_chainlink_aggregator.address).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)

    return deployment


def test_repr(
    web3: Web3,
    deployment: EnzymeDeployment,
    uniswap_v2: UniswapV2Deployment,
    hot_wallet: HotWallet,
    usdc: Contract,
    weth: Contract,
):
    """EnzymeVaultTransaction.__repr__() works.

    Check __repr__() of two different types of txs.
    """

    approve_tx = EnzymeVaultTransaction(
        usdc,
        usdc.functions.approve,
        gas_limit=500_000,
        args=[uniswap_v2.router.address, 500 * 10**6],
    )

    # Check EnzymeVaultTransaction.__repr__
    str(approve_tx)

    expected_incoming_amount = expected_outgoing_amount = 1

    buy_tx = EnzymeVaultTransaction(
        uniswap_v2.router,
        uniswap_v2.router.functions.swapExactTokensForTokens,
        gas_limit=5750_000,
        args=[],
        asset_deltas=[
            AssetDelta(weth.address, expected_incoming_amount),
            AssetDelta(usdc.address, -expected_outgoing_amount),
        ]
    )
    # Check EnzymeVaultTransaction.__repr__
    str(buy_tx)


def test_vault_controlled_wallet_make_buy(
    web3: Web3,
    deployment: EnzymeDeployment,
    uniswap_v2: UniswapV2Deployment,
    hot_wallet: HotWallet,
    deployer: HexAddress,
    usdc: Contract,
    weth: Contract,
    user_1: HexAddress,
    weth_usdc_pair: Contract,
):
    """Buy tokens using vault controlled wallet interface."""

    generic_adapter = deploy_contract(web3, f"enzyme/GenericAdapter.json", deployer, deployment.contracts.integration_manager.address)
    assert generic_adapter.functions.getIntegrationManager().call() == deployment.contracts.integration_manager.address

    comptroller_contract, vault_contract = deployment.create_new_vault(hot_wallet.address, usdc, fund_name="Toholampi Juhannusjami", fund_symbol="JUUH")
    vault = Vault(vault_contract, comptroller_contract, deployment, generic_adapter)
    vault_wallet = VaultControlledWallet(vault, hot_wallet)

    swap_amount = 500 * 10**6

    # Buy in to the vault
    usdc.functions.transfer(user_1, swap_amount).transact({"from": deployer})
    usdc.functions.approve(vault.comptroller.address, 500 * 10**6).transact({"from": user_1})
    vault.comptroller.functions.buyShares(swap_amount, 1).transact({"from": user_1})
    assert usdc.functions.balanceOf(vault.address).call() == swap_amount

    # First approve tokens from the vault
    approve_tx = EnzymeVaultTransaction(
        usdc,
        usdc.functions.approve,
        gas_limit=500_000,
        args=[uniswap_v2.router.address, swap_amount],
    )

    signed = vault_wallet.sign_transaction_with_new_nonce(approve_tx)
    tx_hash = web3.eth.send_raw_transaction(signed.raw_transaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Vault can now trade on Uniswap v2.
    # TODO: This exposes the unsafetiness of the default GenericAdapter implementation
    assert usdc.functions.allowance(vault.generic_adapter.address, uniswap_v2.router.address).call() > 0

    # Prepare the swap parameters
    token_in = usdc
    token_out = weth
    token_in_swap_amount = swap_amount
    path = [token_in.address, token_out.address]
    token_in_amount, token_out_amount = uniswap_v2.router.functions.getAmountsOut(token_in_swap_amount, path).call()

    assert token_in_amount / 10**6 == 500
    assert token_out_amount / 10**18 == pytest.approx(0.31078786125581986)  # 1600 ETH/USD
    slippage_tolenrance = 0.98  # 2%

    # Then we swap them
    buy_tx = EnzymeVaultTransaction(
        uniswap_v2.router,
        uniswap_v2.router.functions.swapExactTokensForTokens,
        gas_limit=1_750_000,
        args=[token_in_swap_amount, 1, path, vault.generic_adapter.address, FOREVER_DEADLINE],
        asset_deltas=[
            AssetDelta(weth.address, int(token_out_amount * slippage_tolenrance)),
            AssetDelta(usdc.address, -token_in_amount),
        ]
    )

    signed = vault_wallet.sign_transaction_with_new_nonce(buy_tx)
    tx_hash = web3.eth.send_raw_transaction(signed.raw_transaction)
    try:
        assert_transaction_success_with_explanation(web3, tx_hash)
    except Exception as e:
        import ipdb ; ipdb.set_trace()
        pass

    assert weth.functions.balanceOf(vault.address).call() > 0
    assert usdc.functions.balanceOf(vault.address).call() == 0
