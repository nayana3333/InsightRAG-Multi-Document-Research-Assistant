import argparse
import json
import time
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="Exercise the authenticated v3 RAG flow.")
    parser.add_argument("pdf_one", type=Path)
    parser.add_argument("pdf_two", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8011")
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=180) as client:
        registration = client.post(
            "/auth/register",
            json={
                "name": "V3 Smoke Test",
                "email": f"smoke-{time.time_ns()}@insightrag.local",
                "password": "StrongSmokePassword3!",
            },
        )
        registration.raise_for_status()
        headers = {"Authorization": f"Bearer {registration.json()['accessToken']}"}

        with args.pdf_one.open("rb") as file:
            first = client.post(
                "/chats", headers=headers, files={"file": (args.pdf_one.name, file, "application/pdf")}
            )
        first.raise_for_status()
        chat_id = first.json()["chatId"]

        try:
            with args.pdf_two.open("rb") as file:
                second = client.post(
                    f"/chats/{chat_id}/documents",
                    headers=headers,
                    files={"file": (args.pdf_two.name, file, "application/pdf")},
                )
            second.raise_for_status()
            documents = client.get(f"/chats/{chat_id}/documents", headers=headers).json()["documents"]

            events = []
            with client.stream(
                "POST",
                f"/chats/{chat_id}/messages/stream",
                headers=headers,
                json={"question": "What topics are discussed across this workspace? Cite the evidence."},
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))

            tokens = "".join(event["token"] for event in events if event["type"] == "token")
            sources = next(event["sources"] for event in events if event["type"] == "sources")
            completed = any(event["type"] == "done" for event in events)
            messages = client.get(f"/chats/{chat_id}/messages", headers=headers).json()["messages"]
            assert len(documents) == 2
            assert tokens and sources and completed
            assert len(messages) == 3
            print(
                json.dumps(
                    {
                        "documents": len(documents),
                        "streamedCharacters": len(tokens),
                        "sources": len(sources),
                        "persistedMessages": len(messages),
                    }
                )
            )
        finally:
            client.delete(f"/chats/{chat_id}", headers=headers)


if __name__ == "__main__":
    main()
