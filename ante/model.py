"""One Bedrock client, shared by both graphs.

Two facts about the hackathon account shape this file:

1. SSO issues session tokens that die every ~12h, so credential failure is a
   normal operating state, not an exception. It gets its own type so the curator
   can checkpoint and resume instead of crashing.
2. The org's service control policy denies the `global.` inference profile in
   every region, and ap-southeast-1 offers *only* `global.` for this model. So
   despite Singapore being the obvious home for Singapore compliance data, the
   only combination that actually invokes is us-east-1 + `us.`. Candidates are
   probed rather than assumed, so this self-corrects if the policy changes.
"""
from __future__ import annotations

import functools
import pathlib

import boto3
from dotenv import load_dotenv
from langchain_aws import ChatBedrockConverse

BASE_MODEL = "anthropic.claude-haiku-4-5-20251001-v1:0"

# Order matters: first one that invokes wins. ap-southeast-1 stays in the list
# so the day the SCP is relaxed, Ante moves to Singapore with no code change.
CANDIDATES = (
    ("us-east-1", f"us.{BASE_MODEL}"),
    ("ap-southeast-1", f"global.{BASE_MODEL}"),
    ("us-east-1", f"global.{BASE_MODEL}"),
)

ENV_FILE = pathlib.Path(__file__).resolve().parent.parent / "env" / ".env"
_EXPIRED = ("ExpiredToken", "InvalidClientTokenId", "ExpiredTokenException")


def load_env() -> None:
    load_dotenv(ENV_FILE, override=False)


def _is_expired(exc: Exception) -> bool:
    return any(marker in str(exc) for marker in _EXPIRED)


def credentials_ok() -> tuple[bool, str]:
    """(alive, detail). Cheap STS call -- no model invoked, nothing billed."""
    load_env()
    try:
        ident = boto3.client("sts", region_name="us-east-1").get_caller_identity()
        return True, f"account {ident['Account']}"
    except Exception as exc:
        if _is_expired(exc):
            return False, "credentials expired -- refresh env/.env"
        return False, f"{type(exc).__name__}: {exc}"


@functools.lru_cache(maxsize=1)
def pick_model() -> tuple[str, str]:
    """First (region, model_id) that actually invokes. One tiny probe, cached."""
    load_env()
    probe = [{"role": "user", "content": [{"text": "ok"}]}]
    last = ""
    for region, model_id in CANDIDATES:
        try:
            boto3.client("bedrock-runtime", region_name=region).converse(
                modelId=model_id, messages=probe, inferenceConfig={"maxTokens": 1})
            return region, model_id
        except Exception as exc:
            if _is_expired(exc):
                raise CredentialsExpired(str(exc)) from exc
            last = f"{region}/{model_id.split('.')[0]}: {type(exc).__name__}"
    raise RuntimeError(f"no usable Bedrock model. Last: {last}")


def chat(temperature: float = 0.0, max_tokens: int = 2000) -> ChatBedrockConverse:
    """The model client. temperature=0 -- this is regulatory work, not writing."""
    region, model_id = pick_model()
    return ChatBedrockConverse(
        model=model_id,
        region_name=region,
        temperature=temperature,
        max_tokens=max_tokens,
    )


class CredentialsExpired(RuntimeError):
    """SSO token has expired. Refresh env/.env and re-run; the graph resumes."""


if __name__ == "__main__":
    alive, detail = credentials_ok()
    print(f"credentials : {'OK' if alive else 'DEAD'}  {detail}")
    if alive:
        region, model_id = pick_model()
        print(f"region      : {region}")
        print(f"model       : {model_id}")
        reply = chat(max_tokens=64).invoke("Reply with exactly: Ante online.")
        print(f"bedrock     : {reply.content!r}")
        print(f"tokens      : {reply.usage_metadata}")
