from __future__ import annotations

import asyncio
import logging
import time

import httpx

from src.config import ACP_AGENT_WALLET, BASESCAN_API_KEY

log = logging.getLogger(__name__)

# USDC on Base chain
USDC_BASE_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
BASESCAN_API = "https://api.basescan.org/api"


class PaymentMonitor:
    """Monitors USDC transfers to the agent wallet on Base chain."""

    def __init__(self) -> None:
        if not ACP_AGENT_WALLET or not ACP_AGENT_WALLET.startswith("0x"):
            raise ValueError(
                "ACP_AGENT_WALLET is empty or invalid. "
                "Set a valid wallet address in .env to receive payments."
            )
        self.wallet = ACP_AGENT_WALLET.lower()
        if not BASESCAN_API_KEY:
            log.warning(
                "BASESCAN_API_KEY is not set — using unauthenticated Basescan API "
                "(5 req/s limit). Set it in .env for reliable payment detection."
            )

    def get_payment_instructions(self, amount: float, job_id: int) -> str:
        return (
            f"Payment required: ${amount:.2f} USDC\n\n"
            f"Send exactly {amount:.2f} USDC (Base chain) to:\n"
            f"`{ACP_AGENT_WALLET}`\n\n"
            f"Network: Base (Chain ID 8453)\n"
            f"Token: USDC ({USDC_BASE_ADDRESS})\n\n"
            f"After sending, I'll detect the payment automatically "
            f"and start your job (Job #{job_id}).\n\n"
            f"Use /paid {job_id} if detection takes more than 2 minutes."
        )

    async def wait_for_payment(
        self,
        amount: float,
        timeout: float = 600.0,
        poll_interval: float = 15.0,
    ) -> dict | None:
        """Poll Base chain for incoming USDC transfer.

        Returns transfer details dict on success, None on timeout.
        """
        start = time.time()
        seen_hashes: set[str] = set()

        # Get initial transfers to establish baseline
        initial = await self._get_recent_transfers()
        for tx in initial:
            seen_hashes.add(tx.get("hash", ""))

        while time.time() - start < timeout:
            await asyncio.sleep(poll_interval)
            try:
                transfers = await self._get_recent_transfers()
                for tx in transfers:
                    tx_hash = tx.get("hash", "")
                    if tx_hash in seen_hashes:
                        continue
                    seen_hashes.add(tx_hash)

                    # Check if it's an incoming USDC transfer to our wallet
                    to_addr = tx.get("to", "").lower()
                    token = tx.get("contractAddress", "").lower()
                    if to_addr != self.wallet:
                        continue
                    if token != USDC_BASE_ADDRESS.lower():
                        continue

                    # USDC has 6 decimals
                    raw_value = int(tx.get("value", "0"))
                    tx_amount = raw_value / 1_000_000

                    # Allow 1% tolerance for rounding
                    if tx_amount >= amount * 0.99:
                        log.info(
                            "Payment detected: %.2f USDC (tx: %s)",
                            tx_amount, tx_hash,
                        )
                        return {
                            "hash": tx_hash,
                            "amount": tx_amount,
                            "from": tx.get("from", ""),
                            "timestamp": tx.get("timeStamp", ""),
                        }
            except Exception as e:
                log.error("Payment poll error: %s", e)

        log.warning("Payment timeout after %.0fs", timeout)
        return None

    async def _get_recent_transfers(self) -> list[dict]:
        """Get recent ERC-20 token transfers to our wallet via Basescan."""
        params = {
            "module": "account",
            "action": "tokentx",
            "address": self.wallet,
            "sort": "desc",
            "page": "1",
            "offset": "20",
        }
        if BASESCAN_API_KEY:
            params["apikey"] = BASESCAN_API_KEY
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(BASESCAN_API, params=params)
                data = resp.json()
                if data.get("status") == "1":
                    return data.get("result", [])
                return []
        except Exception as e:
            log.error("Basescan API error: %s", e)
            return []

    async def verify_payment(self, tx_hash: str, expected_amount: float) -> bool:
        """Verify a specific transaction is a valid USDC payment."""
        params = {
            "module": "account",
            "action": "tokentx",
            "address": self.wallet,
            "sort": "desc",
            "page": "1",
            "offset": "50",
        }
        if BASESCAN_API_KEY:
            params["apikey"] = BASESCAN_API_KEY
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(BASESCAN_API, params=params)
                data = resp.json()
                if data.get("status") != "1":
                    return False
                for tx in data.get("result", []):
                    if tx.get("hash", "").lower() == tx_hash.lower():
                        raw_value = int(tx.get("value", "0"))
                        tx_amount = raw_value / 1_000_000
                        return tx_amount >= expected_amount * 0.99
        except Exception as e:
            log.error("Payment verification error: %s", e)
        return False
