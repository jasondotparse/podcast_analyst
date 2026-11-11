"""Plain-text SES email, using the instance role in beta and no credentials locally."""

import asyncio

import boto3


class EmailClient:
    def __init__(self, sender: str | None):
        self.sender = sender
        self.enabled = bool(sender)

    async def send(self, to: str, subject: str, body: str) -> None:
        def send():
            with boto3.client("sesv2", region_name="us-east-1") as ses:
                ses.send_email(FromEmailAddress=self.sender, Destination={"ToAddresses": [to]},
                    Content={"Simple": {"Subject": {"Data": subject, "Charset": "UTF-8"},
                                        "Body": {"Text": {"Data": body, "Charset": "UTF-8"}}}})
        await asyncio.to_thread(send)
