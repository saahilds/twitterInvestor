"""Resilient Robinhood device-approval polling.

``robin_stocks`` polls ``get_prompts_status`` in a bare ``while True`` and
crashes with ``'NoneType' object is not subscriptable`` when Robinhood returns
429 (``request_get`` yields ``None``). That leaves the bot hammering login and
stuck in cooldown.

We temporarily replace ``_validate_sherrif_id`` for the duration of ``login``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("trading_bot")

# robin_stocks.login() swallows verification exceptions and returns None.
# Stash the last error so our session manager can classify it.
_last_error: BaseException | None = None


class RobinhoodRateLimitedError(RuntimeError):
    """Robinhood rejected prompt polling with 429 / empty responses."""


class RobinhoodVerificationTimeout(TimeoutError):
    """Device approval was not completed within the allotted window."""


def take_last_error() -> BaseException | None:
    global _last_error
    err = _last_error
    _last_error = None
    return err


def _set_last_error(exc: BaseException) -> None:
    global _last_error
    _last_error = exc


def _challenge_status(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    status = payload.get("challenge_status")
    return str(status) if status is not None else None


def validate_sheriff_id(
    device_token: str,
    workflow_id: str,
    *,
    timeout_seconds: int = 180,
    poll_seconds: float = 8.0,
    max_empty_polls: int = 0,
) -> None:
    """Poll Robinhood's sheriff / device-approval workflow until approved.

    ``max_empty_polls``: abort after this many empty/429 responses. ``0`` means
    keep waiting until ``timeout_seconds`` (preferred — early abort burned our
    one reauth try while the user still had time to approve).
    """
    # Import inside so tests can patch robin_stocks without importing it early.
    from robin_stocks.robinhood.helper import request_get, request_post

    global _last_error
    _last_error = None
    print("Starting verification process...")
    pathfinder_url = "https://api.robinhood.com/pathfinder/user_machine/"
    machine_payload = {
        "device_id": device_token,
        "flow": "suv",
        "input": {"workflow_id": workflow_id},
    }
    machine_data = request_post(url=pathfinder_url, payload=machine_payload, json=True)
    if not isinstance(machine_data, dict) or "id" not in machine_data:
        raise RobinhoodVerificationTimeout(
            "Error: No verification ID returned in user-machine response"
        )
    machine_id = machine_data["id"]
    inquiries_url = f"https://api.robinhood.com/pathfinder/inquiries/{machine_id}/user_view/"

    deadline = time.monotonic() + max(30, int(timeout_seconds))
    empty_polls = 0
    prompt_started = False
    poll = max(5.0, float(poll_seconds))

    while time.monotonic() < deadline:
        time.sleep(poll)
        inquiries_response = request_get(inquiries_url)
        if not inquiries_response:
            empty_polls += 1
            logger.warning(
                "robinhood_verification_empty_inquiry",
                extra={
                    "event_type": "robinhood_auth",
                    "empty_polls": empty_polls,
                },
            )
            if max_empty_polls > 0 and empty_polls >= max_empty_polls:
                raise RobinhoodRateLimitedError(
                    "429 Too Many Requests while polling Robinhood verification"
                )
            # Back off hard on empties/429s so one attempt doesn't dig the hole.
            time.sleep(min(120.0, poll * max(1, empty_polls)))
            continue

        empty_polls = 0
        context = inquiries_response.get("context") if isinstance(inquiries_response, dict) else None
        challenge = None
        if isinstance(context, dict):
            challenge = context.get("sheriff_challenge")

        if isinstance(challenge, dict):
            challenge_type = challenge.get("type")
            challenge_status = challenge.get("status")
            challenge_id = challenge.get("id")

            if challenge_type == "prompt" and challenge_id:
                if not prompt_started:
                    print("Check robinhood app for device approvals method...")
                    prompt_started = True
                prompt_url = (
                    f"https://api.robinhood.com/push/{challenge_id}/get_prompts_status/"
                )
                while time.monotonic() < deadline:
                    time.sleep(poll)
                    prompt_status = request_get(url=prompt_url)
                    status = _challenge_status(prompt_status)
                    if status == "validated":
                        break
                    if prompt_status is None:
                        empty_polls += 1
                        logger.warning(
                            "robinhood_device_prompt_empty",
                            extra={
                                "event_type": "robinhood_auth",
                                "empty_polls": empty_polls,
                            },
                        )
                        if max_empty_polls > 0 and empty_polls >= max_empty_polls:
                            raise RobinhoodRateLimitedError(
                                "429 Too Many Requests for get_prompts_status"
                            )
                        time.sleep(min(120.0, poll * max(1, empty_polls)))
                        continue
                    empty_polls = 0
                else:
                    raise RobinhoodVerificationTimeout(
                        f"Device approval not confirmed within {timeout_seconds}s"
                    )
                break

            if challenge_status == "validated":
                print("Verification successful!")
                break

            if challenge_type in {"sms", "email"} and challenge_status == "issued":
                # Non-interactive bot: cannot collect SMS/email codes.
                raise RobinhoodVerificationTimeout(
                    f"Robinhood requested {challenge_type} verification; "
                    "use device approval in the app instead"
                )

    else:
        raise RobinhoodVerificationTimeout(
            f"Device approval not confirmed within {timeout_seconds}s"
        )

    # Confirm workflow approval (same as upstream, with None/timeout guards).
    inquiries_url = f"https://api.robinhood.com/pathfinder/inquiries/{machine_id}/user_view/"
    retry_attempts = 5
    while time.monotonic() < deadline and retry_attempts > 0:
        try:
            inquiries_payload = {"sequence": 0, "user_input": {"status": "continue"}}
            inquiries_response = request_post(
                url=inquiries_url, payload=inquiries_payload, json=True
            )
        except Exception as exc:
            retry_attempts -= 1
            logger.warning(
                "robinhood_workflow_status_request_failed",
                extra={"event_type": "robinhood_auth", "error": str(exc)},
            )
            time.sleep(poll_seconds)
            continue

        if not inquiries_response:
            retry_attempts -= 1
            time.sleep(poll_seconds)
            continue

        type_context = (
            inquiries_response.get("type_context")
            if isinstance(inquiries_response, dict)
            else None
        )
        if isinstance(type_context, dict) and type_context.get("result") == "workflow_status_approved":
            print("Verification successful!")
            return

        workflow_status = None
        if isinstance(inquiries_response, dict):
            workflow = inquiries_response.get("verification_workflow")
            if isinstance(workflow, dict):
                workflow_status = workflow.get("workflow_status")
        if workflow_status == "workflow_status_approved":
            print("Workflow status approved! Proceeding with login...")
            return

        time.sleep(poll_seconds)
        retry_attempts -= 1

    # Upstream assumes approval after retries; keep that soft fallback so a
    # completed phone tap still finishes login when the status endpoint lags.
    print("Max workflow polls reached. Assuming login approved and proceeding.")


def _run_validate(
    device_token: str,
    workflow_id: str,
    *,
    timeout_seconds: int,
    poll_seconds: float,
    max_empty_polls: int,
) -> None:
    try:
        validate_sheriff_id(
            device_token,
            workflow_id,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            max_empty_polls=max_empty_polls,
        )
    except BaseException as exc:
        _set_last_error(exc)
        raise


@contextmanager
def patch_device_approval(
    *,
    timeout_seconds: int = 180,
    poll_seconds: float = 8.0,
    max_empty_polls: int = 0,
) -> Iterator[None]:
    """Patch robin_stocks device-approval validation for one login attempt."""
    import robin_stocks.robinhood.authentication as auth_mod

    original = auth_mod._validate_sherrif_id

    def _patched(device_token: str, workflow_id: str) -> None:
        _run_validate(
            device_token,
            workflow_id,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            max_empty_polls=max_empty_polls,
        )

    auth_mod._validate_sherrif_id = _patched  # type: ignore[assignment]
    try:
        yield
    finally:
        auth_mod._validate_sherrif_id = original  # type: ignore[assignment]
