"""Vault owner wallet implementation.
"""

from web3 import Web3

from eth_defi.enzyme.vault import Vault
from eth_defi.hotwallet import HotWallet, SignedTransactionWithNonce


class VaultControlledWallet:
    """A vault wallet.

    Allows you to sign and broadcast transactions concerning Enzyme's vault as a vault owner.

    """

    def __init__(self,
            vault: Vault,
            hot_wallet: HotWallet):
        """Create a vault controlling wallet.

        :param hot_wallet:
            The fund deployment account as a EOA wallet.
        """
        self.vault = vault
        self.hot_wallet = hot_wallet

    @property
    def address(self):
        """Get the vault address."""
        return self.account.address

    def sync_nonce(self, web3: Web3):
        """Read the current nonce"""
        self.hot_wallet.sync_nonce(web3)

    def allocate_nonce(self) -> int:
        """Get the next free available nonce to be used with a transaction.

        Ethereum tx nonces are a counter.

        Increase the nonce counter
        """
        return self.hot_wallet.allocate_nonce()

    def sign_transaction_with_new_nonce(self, tx: dict) -> SignedTransactionWithNonce:
        """Signs a transaction and allocates a nonce for it.

        :param: Ethereum transaction data as a dict. This is modified in-place to include nonce.
        """
        assert "nonce" not in tx
        tx["nonce"] = self.allocate_nonce()
        _signed = self.account.sign_transaction(tx)
        decode_signed_transaction(_signed.rawTransaction)
        signed = SignedTransactionWithNonce(
            rawTransaction=_signed.rawTransaction,
            hash=_signed.hash,
            v=_signed.v,
            r=_signed.r,
            s=_signed.s,
            nonce=tx["nonce"],
            source=tx,
        )
        return signed

    def get_native_currency_balance(self, web3: Web3) -> Decimal:
        """Get the balance of the native currency (ETH, BNB, MATIC) of the wallet.

        Useful to check if you have enough cryptocurrency for the gas fees.
        """
        balance = web3.eth.get_balance(self.address)
        return web3.from_wei(balance, "ether")

    @staticmethod
    def from_private_key(key: str) -> "HotWallet":
        """Create a hot wallet from a private key that is passed in as a hex string.

        Add the key to web3 signing chain.

        Example:

        .. code-block::

            # Generated with  openssl rand -hex 32
            wallet = HotWallet.from_private_key("0x54c137e27d2930f7b3433249c5f07b37ddcfea70871c0a4ef9e0f65655faf957")

        :param key: 0x prefixed hex string
        :return: Ready to go hot wallet account
        """
        assert key.startswith("0x")
        account = Account.from_key(key)
        return HotWallet(account)



