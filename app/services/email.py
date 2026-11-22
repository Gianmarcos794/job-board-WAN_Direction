"""Outbound email. The sender only logs messages; no real email is sent."""

from __future__ import annotations

import logging
from dataclasses import dataclass


@dataclass
class EmailMessage:
    to: str
    subject: str
    body: str


class EmailSender:
    def send(self, message: EmailMessage) -> None:
        logging.getLogger(__name__).info(
            "EMAIL to=%s subject=%r\n%s", message.to, message.subject, message.body
        )


def get_email_sender() -> EmailSender:
    return EmailSender()
