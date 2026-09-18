"""Typed natural-request admission that delegates to existing JL surfaces."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .ipc import IPCRequestEnvelope, IPCResponseEnvelope

StructuredHandler = Callable[[IPCRequestEnvelope], IPCResponseEnvelope]

_ACTION_WORDS = frozenset(
    {
        "activate",
        "click",
        "close",
        "copy",
        "delete",
        "drag",
        "email",
        "enter",
        "erase",
        "focus",
        "message",
        "move",
        "open",
        "press",
        "purchase",
        "remove",
        "run",
        "schedule",
        "scroll",
        "send",
        "set",
        "submit",
        "trade",
        "transfer",
        "type",
        "write",
    }
)
_RELATIVE_UNITS = {
    "m": "m",
    "min": "m",
    "minute": "m",
    "minutes": "m",
    "h": "h",
    "hour": "h",
    "hours": "h",
    "d": "d",
    "day": "d",
    "days": "d",
}
_MAX_TEXT = 16_384


@dataclass(frozen=True, slots=True)
class _ReminderDraft:
    name: str
    schedule: str
    note: str


@dataclass(frozen=True, slots=True)
class _ComputerDraft:
    arguments: Mapping[str, Any]
    permissions: tuple[str, ...]
    resolved_target: str


class AssistantAdmission:
    """Admit one typed or voice request into already-reviewed boundaries.

    This component does not plan, approve, schedule ticks, or execute tools
    directly. It admits a bounded request into the existing conversation,
    structured control, or automation management surface.
    """

    def __init__(
        self,
        *,
        turn_runner: Callable[[str], str],
        control_handler: StructuredHandler,
        automation_handler: StructuredHandler,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.turn_runner = turn_runner
        self.control_handler = control_handler
        self.automation_handler = automation_handler
        self.clock = clock or (lambda: datetime.now().astimezone())

    def handle(self, envelope: IPCRequestEnvelope) -> IPCResponseEnvelope:
        print(
            "jl_voice_trace "
            f"stage=assistant_admission_enter mono_ms={time.monotonic_ns() // 1_000_000} "
            f"mode={envelope.payload.get('input_mode', 'typed')}",
            flush=True,
        )
        try:
            text, foreground_app, timezone = self._decode(envelope.payload)
        except ValueError as error:
            return IPCResponseEnvelope.failure(
                envelope.request_id, "malformed_payload", str(error)
            )
        lowered = text.casefold()

        reminder = self._parse_reminder(text, timezone)
        if reminder is not None:
            if isinstance(reminder, str):
                return self._clarification(envelope, reminder)
            return self._create_reminder(envelope, reminder)

        computer = self._parse_computer(text, foreground_app)
        if computer is not None:
            if isinstance(computer, str):
                return self._clarification(envelope, computer)
            return self._run_computer(envelope, computer, foreground_app)

        if self._looks_action_like(lowered):
            return self._clarification(
                envelope,
                "request looks action-like but is outside the typed Phase 7 slice",
            )

        try:
            print(
                f"jl_voice_trace stage=hermes_brain_request mono_ms={time.monotonic_ns() // 1_000_000}",
                flush=True,
            )
            reply = self.turn_runner(text).strip()[:32_768]
            print(
                "jl_voice_trace "
                f"stage=hermes_brain_response mono_ms={time.monotonic_ns() // 1_000_000} "
                f"chars={len(reply)}",
                flush=True,
            )
        except Exception:
            print(
                "jl_voice_trace "
                f"stage=hermes_brain_failed mono_ms={time.monotonic_ns() // 1_000_000} "
                "error_type=exception",
                flush=True,
            )
            return IPCResponseEnvelope.failure(
                envelope.request_id,
                "conversation_failed",
                "conversation delegation failed",
            )
        return IPCResponseEnvelope.success(
            envelope.request_id,
            {"state": "conversation_completed", "reply": reply},
        )

    @staticmethod
    def _decode(payload: Mapping[str, Any]) -> tuple[str, str, str]:
        if set(payload).difference(
            {"text", "input_mode", "foreground_app", "timezone"}
        ):
            raise ValueError("assistant payload has unknown fields")
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > _MAX_TEXT:
            raise ValueError("text must be non-empty bounded text")
        input_mode = payload.get("input_mode", "typed")
        if input_mode not in ("typed", "voice"):
            raise ValueError("assistant-request accepts typed or voice input only")
        foreground = payload.get("foreground_app", "")
        if not isinstance(foreground, str) or len(foreground) > 256:
            raise ValueError("foreground_app must be bounded text")
        timezone = payload.get("timezone", "UTC")
        if not isinstance(timezone, str) or not timezone or len(timezone) > 64:
            raise ValueError("timezone must be bounded text")
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone is unavailable") from error
        return text.strip(), foreground.strip(), timezone

    def _parse_reminder(
        self, text: str, timezone: str
    ) -> _ReminderDraft | str | None:
        lowered = text.casefold()
        if "remind me" not in lowered and "reminder" not in lowered:
            if "schedule" in lowered:
                return "only reminder scheduling is supported in this slice"
            return None

        patterns = (
            r"^remind me to (?P<note>.+?) (?P<schedule>in .+)$",
            r"^remind me (?P<schedule>in .+?) to (?P<note>.+)$",
            r"^remind me to (?P<note>.+?) (?P<schedule>every .+)$",
            r"^every (?P<schedule_body>.+?) remind me to (?P<note>.+)$",
            r"^remind me to (?P<note>.+?) (?P<schedule>(?:today|tomorrow) at .+)$",
            r"^remind me to (?P<note>.+?) (?P<schedule>\d{4}-\d{2}-\d{2} at .+)$",
        )
        for pattern in patterns:
            match = re.match(pattern, text.strip(), re.IGNORECASE)
            if match is None:
                continue
            groups = match.groupdict()
            note = _clean_note(groups.get("note", ""))
            if not note:
                return "reminder text is missing"
            raw_schedule = groups.get("schedule") or f"every {groups['schedule_body']}"
            schedule = self._normalize_schedule(raw_schedule, timezone)
            if schedule is None:
                return "reminder time is ambiguous"
            return _ReminderDraft(
                name=_summary(f"Reminder: {note}", 128),
                schedule=schedule,
                note=note,
            )
        return "reminder request is not an unambiguous supported reminder"

    def _normalize_schedule(self, raw: str, timezone: str) -> str | None:
        value = " ".join(raw.strip().split())
        relative = re.fullmatch(
            r"in (?P<count>[1-9]\d{0,3})\s*(?P<unit>m|min|minutes?|h|hours?|d|days?)",
            value,
            re.IGNORECASE,
        )
        if relative is not None:
            unit = _RELATIVE_UNITS[relative.group("unit").casefold()]
            return f"in {relative.group('count')}{unit}"

        interval = re.fullmatch(
            r"every (?P<count>[1-9]\d{0,3})\s*"
            r"(?P<unit>m|min|minutes?|h|hours?|d|days?)",
            value,
            re.IGNORECASE,
        )
        if interval is not None:
            unit = _RELATIVE_UNITS[interval.group("unit").casefold()]
            return f"every {interval.group('count')}{unit}"

        absolute = re.fullmatch(
            r"(?P<day>today|tomorrow|\d{4}-\d{2}-\d{2}) at "
            r"(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d)",
            value,
            re.IGNORECASE,
        )
        if absolute is None:
            return None
        zone = ZoneInfo(timezone)
        now = self.clock().astimezone(zone)
        day = absolute.group("day").casefold()
        try:
            if day == "today":
                date = now.date()
            elif day == "tomorrow":
                date = (now + timedelta(days=1)).date()
            else:
                date = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            return None
        scheduled = datetime(
            date.year,
            date.month,
            date.day,
            int(absolute.group("hour")),
            int(absolute.group("minute")),
            tzinfo=zone,
        )
        if scheduled <= now:
            return None
        return scheduled.isoformat()

    @staticmethod
    def _parse_computer(
        text: str, foreground_app: str
    ) -> _ComputerDraft | str | None:
        lowered = " ".join(text.casefold().split())
        if lowered in {
            "list apps",
            "list applications",
            "show apps",
            "show applications",
        }:
            return _ComputerDraft(
                arguments={"action": "list_apps"},
                permissions=("local.read",),
                resolved_target="desktop",
            )
        if lowered in {"list windows", "show windows"}:
            return _ComputerDraft(
                arguments={"action": "list_windows"},
                permissions=("local.read",),
                resolved_target=foreground_app or "desktop",
            )
        if lowered in {
            "capture screen",
            "capture the screen",
            "capture current screen",
            "capture the current screen",
            "take screenshot",
            "take a screenshot",
            "screenshot",
        }:
            return _ComputerDraft(
                arguments={"action": "capture", "mode": "ax"},
                permissions=("screen.capture",),
                resolved_target=foreground_app or "current screen",
            )
        computer_terms = {
            "app",
            "button",
            "click",
            "desktop",
            "focus",
            "key",
            "screen",
            "scroll",
            "type",
            "window",
        }
        if any(term in lowered.split() for term in computer_terms):
            return (
                "computer-use first slice allows only capture, list_apps, "
                "and list_windows"
            )
        return None

    def _run_computer(
        self,
        envelope: IPCRequestEnvelope,
        draft: _ComputerDraft,
        foreground_app: str,
    ) -> IPCResponseEnvelope:
        payload = _control_payload(draft, foreground_app)
        prepared = self.control_handler(
            _copy_envelope(envelope, operation="prepare", payload=payload)
        )
        if not prepared.ok:
            if prepared.error_code == "policy_denied":
                return IPCResponseEnvelope.success(
                    envelope.request_id,
                    {"state": "denied", "reason": prepared.error_code},
                )
            return prepared
        assert prepared.result is not None
        state = prepared.result.get("state")
        if state == "awaiting_approval":
            return IPCResponseEnvelope.success(
                envelope.request_id,
                {
                    "state": "awaiting_approval",
                    "intent": "computer_action",
                    **dict(prepared.result),
                },
            )
        if state != "prepared":
            return IPCResponseEnvelope.failure(
                envelope.request_id,
                "assistant_action_failed",
                "structured action did not prepare",
            )
        executed = self.control_handler(
            _copy_envelope(envelope, operation="execute", payload=payload)
        )
        if not executed.ok:
            return executed
        assert executed.result is not None
        return IPCResponseEnvelope.success(
            envelope.request_id,
            {
                "state": "action_completed",
                "intent": "computer_action",
                "action": draft.arguments["action"],
                "output": executed.result.get("output"),
            },
        )

    def _create_reminder(
        self, envelope: IPCRequestEnvelope, reminder: _ReminderDraft
    ) -> IPCResponseEnvelope:
        created = self.automation_handler(
            _copy_envelope(
                envelope,
                operation="automation-create",
                payload={
                    "name": reminder.name,
                    "schedule": reminder.schedule,
                    "note": reminder.note,
                },
            )
        )
        if not created.ok:
            return created
        assert created.result is not None
        return IPCResponseEnvelope.success(
            envelope.request_id,
            {
                "state": "schedule_created_paused",
                "intent": "schedule",
                "job": created.result["job"],
            },
        )

    @staticmethod
    def _looks_action_like(lowered: str) -> bool:
        words = set(re.findall(r"[a-z]+", lowered))
        return bool(words.intersection(_ACTION_WORDS))

    @staticmethod
    def _clarification(
        envelope: IPCRequestEnvelope, reason: str
    ) -> IPCResponseEnvelope:
        return IPCResponseEnvelope.success(
            envelope.request_id,
            {"state": "clarification_required", "reason": reason},
        )


def _control_payload(
    draft: _ComputerDraft, foreground_app: str
) -> dict[str, object]:
    return {
        "capability_id": "core.hermes.computer-use",
        "action": {
            "action": "computer_use",
            "normalized_arguments": dict(draft.arguments),
            "requested_permissions": list(draft.permissions),
            "resolved_target": draft.resolved_target,
            "foreground_app": foreground_app,
            "approval_surface_available": True,
        },
        "route": {
            "category": "simple",
            "required_abilities": ["text", "tool-calling"],
            "local_only": True,
            "off_device_allowed": False,
        },
        "candidates": [
            {
                "id": "native.validation.local",
                "provider": "native-validation",
                "model": "deterministic-boundary",
                "abilities": ["text", "tool-calling"],
                "health": "healthy",
                "local": True,
                "data_residency": "device",
                "cost_class": 0,
            }
        ],
    }


def _copy_envelope(
    envelope: IPCRequestEnvelope, *, operation: str, payload: Mapping[str, Any]
) -> IPCRequestEnvelope:
    return IPCRequestEnvelope(
        protocol_version=envelope.protocol_version,
        request_id=envelope.request_id,
        caller_id=envelope.caller_id,
        session_id=envelope.session_id,
        operation=operation,
        payload=payload,
        credential=envelope.credential,
    )


def _clean_note(value: str) -> str:
    note = " ".join(value.strip().split())
    note = re.sub(r"^(to\s+)+", "", note, flags=re.IGNORECASE)
    return note[:4096]


def _summary(value: str, maximum: int) -> str:
    value = " ".join(value.strip().split())
    return value if len(value) <= maximum else value[: maximum - 3].rstrip() + "..."
