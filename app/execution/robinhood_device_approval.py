"""Resilient Robinhood device-approval handling.

``robin_stocks`` polls ``get_prompts_status`` in a bare ``while True`` and
crashes / 429-loops when Robinhood rate-limits that endpoint. We patch
``_validate_sherrif_id`` and prefer an explicit "already approved" signal over
hammering ``get_prompts_status``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("trading_bot")

# robin_stocks.login() swallows verification exceptions and returns None.
# Stash the last error so our session manager can classify it.
_last_error: BaseException | None = None

# Set by POST /robinhood/reauth/confirm-approval when the user has tapped Yes.
_approval_confirmed = threading.Event()
# Set once Robinhood has issued a device-approval prompt for this login.
_prompt_ready = threading.Event()


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


def clear_approval_signals() -> None:
    _approval_confirmed.clear()
    _prompt_ready.clear()


def signal_approval_confirmed() -> bool:
    """Mark the in-flight device prompt as approved (skip get_prompts_status)."""
    if not _prompt_ready.is_set():
        return False
    _approval_confirmed.set()
    logger.info(
        "robinhood_approval_confirmed_by_user",
        extra={"event_type": "robinhood_auth"},
    )
    return True


def is_prompt_ready() -> bool:
    return _prompt_ready.is_set()


def is_approval_confirmed() -> bool:
    return _approval_confirmed.is_set()


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
    skip_status_poll: bool = True,
) -> None:
    """Drive Robinhood's sheriff / device-approval workflow until approved.

    When ``skip_status_poll`` is true (default), after a device prompt is issued
    we wait for ``signal_approval_confirmed()`` instead of polling
    ``get_prompts_status`` (the endpoint that has been 429'ing us for days).
    """
    from robin_stocks.robinhood.helper import request_get, request_post

    global _last_error
    _last_error = None
    clear_approval_signals()
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
                    print(
                        "After approving, POST /robinhood/reauth/confirm-approval "
                        "(do not rely on get_prompts_status)."
                    )
                    prompt_started = True
                    _prompt_ready.set()
                    logger.info(
                        "robinhood_device_prompt_ready",
                        extra={"event_type": "robinhood_auth"},
                    )

                if skip_status_poll:
                    _wait_for_user_approval(deadline=deadline, poll_seconds=poll)
                else:
                    _poll_prompt_status(
                        challenge_id=str(challenge_id),
                        deadline=deadline,
                        poll_seconds=poll,
                        max_empty_polls=max_empty_polls,
                    )
                break

            if challenge_status == "validated":
                print("Verification successful!")
                break

            if challenge_type in {"sms", "email"} and challenge_status == "issued":
                raise RobinhoodVerificationTimeout(
                    f"Robinhood requested {challenge_type} verification; "
                    "use device approval in the app instead"
                )

    else:
        raise RobinhoodVerificationTimeout(
            f"Device approval not confirmed within {timeout_seconds}s"
        )

    _finish_workflow(
        machine_id=str(machine_id),
        deadline=deadline,
        poll_seconds=poll,
    )


def _wait_for_user_approval(*, deadline: float, poll_seconds: float) -> None:
    """Block until confirm-approval is signaled (no get_prompts_status)."""
    logger.info(
        "robinhood_waiting_for_approval_confirm",
        extra={"event_type": "robinhood_auth"},
    )
    while time.monotonic() < deadline:
        remaining = max(0.5, deadline - time.monotonic())
        if _approval_confirmed.wait(timeout=min(poll_seconds, remaining)):
            print("Approval confirmed by operator — continuing login workflow...")
            return
    raise RobinhoodVerificationTimeout(
        "Device approval not confirmed within timeout "
        "(POST /robinhood/reauth/confirm-approval after tapping Yes in the app)"
    )


def _poll_prompt_status(
    *,
    challenge_id: str,
    deadline: float,
    poll_seconds: float,
    max_empty_polls: int,
) -> None:
    from robin_stocks.robinhood.helper import request_get

    prompt_url = f"https://api.robinhood.com/push/{challenge_id}/get_prompts_status/"
    empty_polls = 0
    while time.monotonic() < deadline:
        if _approval_confirmed.is_set():
            print("Approval confirmed by operator — skipping status poll...")
            return
        time.sleep(poll_seconds)
        prompt_status = request_get(url=prompt_url)
        status = _challenge_status(prompt_status)
        if status == "validated":
            return
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
            time.sleep(min(120.0, poll_seconds * max(1, empty_polls)))
            continue
        empty_polls = 0
    raise RobinhoodVerificationTimeout("Device approval not confirmed within timeout")


def _finish_workflow(*, machine_id: str, deadline: float, poll_seconds: float) -> None:
    from robin_stocks.robinhood.helper import request_post

    inquiries_url = f"https://api.robinhood.com/pathfinder/inquiries/{machine_id}/user_view/"
    retry_attempts = 8
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

    # Soft fallback — phone tap may have completed even if status JSON lags.
    print("Max workflow polls reached. Assuming login approved and proceeding.")


def _run_validate(
    device_token: str,
    workflow_id: str,
    *,
    timeout_seconds: int,
    poll_seconds: float,
    max_empty_polls: int,
    skip_status_poll: bool,
) -> None:
    try:
        validate_sheriff_id(
            device_token,
            workflow_id,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            max_empty_polls=max_empty_polls,
            skip_status_poll=skip_status_poll,
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
    skip_status_poll: bool = True,
) -> Iterator[None]:
    """Patch robin_stocks device-approval validation for one login attempt."""
    import robin_stocks.robinhood.authentication as auth_mod

    original = auth_mod._validate_sherrif_id
    clear_approval_signals()

    def _patched(device_token: str, workflow_id: str) -> None:
        _run_validate(
            device_token,
            workflow_id,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            max_empty_polls=max_empty_polls,
            skip_status_poll=skip_status_poll,
        )

    auth_mod._validate_sherrif_id = _patched  # type: ignore[assignment]
    try:
        yield
    finally:
        auth_mod._validate_sherrif_id = original  # type: ignore[assignment]
