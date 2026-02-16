from __future__ import annotations

import abc


class IntakeSource(abc.ABC):
    """Abstract base class for demand intake sources."""

    @abc.abstractmethod
    async def start(self) -> None:
        """Start listening for incoming requests."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down the intake source."""

    @abc.abstractmethod
    async def send_message(self, client_id: str, message: str) -> None:
        """Send a message back to a client."""
