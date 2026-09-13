"""Thin translation from an exact JL projection to Hermes' public agent surface."""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from .control_plane import HermesInvocationProjection


class ExecutionErrorCategory(StrEnum):
    POLICY_DENIED = "policy_denied"
    APPROVAL_INVALID = "approval_invalid"
    STALE_PREPARATION = "stale_preparation"
    DUPLICATE_EXECUTION = "duplicate_execution"
    ROUTING_MISMATCH = "routing_mismatch"
    UPSTREAM_DENIED = "upstream_denied"
    PROVIDER_ERROR = "provider_error"
    INVALID_REQUEST = "invalid_request"
    INVALID_RESULT = "invalid_result"
    RUNTIME_ERROR = "runtime_error"


class HermesExecutionStatus(StrEnum):
    COMPLETED = "completed"
    DENIED = "denied"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class HermesRuntimeRequest:
    provider: str
    model: str
    fallback_routes: tuple[tuple[str, str], ...]
    enabled_toolsets: tuple[str, ...]
    allowed_tool: str
    session_id: str
    task_id: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class HermesRuntimeResult:
    status: HermesExecutionStatus
    output: str | None = None
    error_category: ExecutionErrorCategory | None = None


class HermesRuntime(Protocol):
    """Replaceable Hermes runtime seam; deterministic fakes implement this in tests."""

    def run(self, request: HermesRuntimeRequest) -> HermesRuntimeResult: ...


@dataclass(frozen=True, slots=True)
class _AuthorizedHermesCommand:
    projection: HermesInvocationProjection
    request_id: str
    authority: object


class HermesExecutionAdapter:
    """Accept only gate-issued commands and preserve the exact JL route/tool bounds."""

    _TOOLSETS: Mapping[str, frozenset[str]] = {
        "file": frozenset({"read_file", "write_file", "patch", "search_files"}),
        "terminal": frozenset({"terminal", "process_manage"}),
        "browser": frozenset(
            {
                "browser_navigate",
                "browser_snapshot",
                "browser_click",
                "browser_type",
                "browser_scroll",
                "browser_back",
                "browser_press",
                "browser_get_images",
                "browser_vision",
                "browser_console",
                "browser_cdp",
                "browser_dialog",
            }
        ),
        "memory": frozenset({"memory"}),
    }

    def __init__(self, runtime: HermesRuntime) -> None:
        self.runtime = runtime
        self.__authority = object()

    def _issue_command(
        self, projection: HermesInvocationProjection, request_id: str
    ) -> _AuthorizedHermesCommand:
        """Internal handoff used only after the execution gate revalidates state."""
        return _AuthorizedHermesCommand(projection, request_id, self.__authority)

    def _execute(self, command: _AuthorizedHermesCommand) -> HermesRuntimeResult:
        if command.authority is not self.__authority:
            return HermesRuntimeResult(
                HermesExecutionStatus.DENIED,
                error_category=ExecutionErrorCategory.POLICY_DENIED,
            )
        try:
            request = self._translate(command.projection, command.request_id)
            result = self.runtime.run(request)
        except ValueError:
            return HermesRuntimeResult(
                HermesExecutionStatus.DENIED,
                error_category=ExecutionErrorCategory.INVALID_REQUEST,
            )
        except Exception:
            return HermesRuntimeResult(
                HermesExecutionStatus.FAILED,
                error_category=ExecutionErrorCategory.RUNTIME_ERROR,
            )
        if not isinstance(result, HermesRuntimeResult):
            return HermesRuntimeResult(
                HermesExecutionStatus.FAILED,
                error_category=ExecutionErrorCategory.INVALID_RESULT,
            )
        if result.status is HermesExecutionStatus.COMPLETED:
            return result
        if result.status is HermesExecutionStatus.DENIED:
            return HermesRuntimeResult(
                HermesExecutionStatus.DENIED,
                error_category=result.error_category
                or ExecutionErrorCategory.UPSTREAM_DENIED,
            )
        return HermesRuntimeResult(
            HermesExecutionStatus.FAILED,
            error_category=result.error_category
            or ExecutionErrorCategory.RUNTIME_ERROR,
        )

    def _translate(
        self, projection: HermesInvocationProjection, request_id: str
    ) -> HermesRuntimeRequest:
        if projection.entrypoint_kind != "hermes-tool":
            raise ValueError("Phase 3B supports only Hermes toolset projections")
        tools = self._TOOLSETS.get(projection.entrypoint_address)
        if tools is None or projection.action not in tools:
            raise ValueError("action is outside the projected Hermes toolset")
        arguments = json.loads(projection.arguments_json)
        if not isinstance(arguments, dict):
            raise ValueError("Hermes action arguments must be an object")
        arguments_json = json.dumps(
            arguments,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return HermesRuntimeRequest(
            provider=projection.provider,
            model=projection.model,
            fallback_routes=projection.fallback_routes,
            enabled_toolsets=(projection.entrypoint_address,),
            allowed_tool=projection.action,
            session_id=projection.session_id,
            task_id=request_id,
            arguments_json=arguments_json,
        )


class HermesAIAgentRuntime:
    """Lazy production bridge to pinned ``AIAgent`` and Hermes tool dispatch.

    No import or provider call occurs until ``run``. Credentials and provider
    resolution remain wholly owned by Hermes.
    """

    def __init__(
        self,
        hermes_root: str | Path,
        *,
        agent_loader: Callable[[], type[Any]] | None = None,
    ) -> None:
        self.hermes_root = Path(hermes_root)
        self._agent_loader = agent_loader or self._load_agent

    def _load_agent(self) -> type[Any]:
        root = str(self.hermes_root)
        if root not in sys.path:
            sys.path.insert(0, root)
        module = importlib.import_module("run_agent")
        return module.AIAgent

    def run(self, request: HermesRuntimeRequest) -> HermesRuntimeResult:
        agent_type = self._agent_loader()
        allowed_providers = list(
            dict.fromkeys(
                [
                    request.provider,
                    *(provider for provider, _ in request.fallback_routes),
                ]
            )
        )
        fallback = [
            {"provider": provider, "model": model}
            for provider, model in request.fallback_routes
        ]
        agent = agent_type(
            provider=request.provider,
            requested_provider=request.provider,
            model=request.model,
            fallback_model=fallback,
            providers_allowed=allowed_providers,
            providers_order=allowed_providers,
            enabled_toolsets=list(request.enabled_toolsets),
            session_id=request.session_id,
            platform="jl-runtime",
            quiet_mode=True,
            save_trajectories=False,
            max_iterations=20,
            skip_context_files=True,
            skip_background_review=True,
        )
        arguments = json.loads(request.arguments_json)
        if not isinstance(arguments, dict):
            raise ValueError("Hermes action arguments must be an object")
        helpers = importlib.import_module("agent.agent_runtime_helpers")
        raw_result = helpers.invoke_tool(
            agent,
            request.allowed_tool,
            arguments,
            request.task_id,
        )
        if not isinstance(raw_result, str):
            raw_result = json.dumps(raw_result, ensure_ascii=False, default=str)
        try:
            result = json.loads(raw_result)
        except json.JSONDecodeError:
            result = None
        if isinstance(result, dict) and result.get("error"):
            blocked = result.get("status") in {"blocked", "pending_approval"}
            category = (
                ExecutionErrorCategory.UPSTREAM_DENIED
                if blocked
                else ExecutionErrorCategory.PROVIDER_ERROR
            )
            status = (
                HermesExecutionStatus.DENIED
                if category is ExecutionErrorCategory.UPSTREAM_DENIED
                else HermesExecutionStatus.FAILED
            )
            return HermesRuntimeResult(status, error_category=category)
        return HermesRuntimeResult(
            HermesExecutionStatus.COMPLETED,
            output=raw_result,
        )
