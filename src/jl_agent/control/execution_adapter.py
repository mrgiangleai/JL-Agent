"""Thin translation from an exact JL projection to Hermes' public tool surface."""

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
    TARGET_CONTEXT_CHANGED = "target_context_changed"
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
        "computer_use": frozenset({"computer_use"}),
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


class HermesToolRuntime:
    """Lazy bridge to pinned Hermes' public, middleware-preserving dispatcher.

    The exact pinned symbols are ``model_tools.handle_function_call`` ->
    ``model_tools._apply_request_middleware`` ->
    ``model_tools._pre_dispatch_guards`` -> ``model_tools._execute_tool`` ->
    ``hermes_cli.middleware.run_tool_execution_middleware`` ->
    ``tools.registry.ToolRegistry.dispatch`` ->
    ``tools.computer_use.tool.handle_computer_use`` (registered by
    ``tools.computer_use_tool``). The JL adapter has already constrained the
    tool and toolset before this bridge runs, so no LLM agent, provider client,
    or generic raw-tool endpoint is needed.
    """

    def __init__(
        self,
        hermes_root: str | Path,
        *,
        dispatcher_loader: Callable[[], Callable[..., Any]] | None = None,
    ) -> None:
        self.hermes_root = Path(hermes_root)
        self._dispatcher_loader = dispatcher_loader or self._load_dispatcher

    def _load_dispatcher(self) -> Callable[..., Any]:
        root = str(self.hermes_root)
        if root not in sys.path:
            sys.path.insert(0, root)
        module = importlib.import_module("model_tools")
        return module.handle_function_call

    def run(self, request: HermesRuntimeRequest) -> HermesRuntimeResult:
        arguments = json.loads(request.arguments_json)
        if not isinstance(arguments, dict):
            raise ValueError("Hermes action arguments must be an object")
        dispatcher = self._dispatcher_loader()
        raw_result = dispatcher(
            request.allowed_tool,
            arguments,
            request.task_id,
            session_id=request.session_id,
            enabled_tools=[request.allowed_tool],
            enabled_toolsets=list(request.enabled_toolsets),
            disabled_toolsets=[],
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


# Compatibility for existing JL imports. This alias no longer constructs AIAgent.
HermesAIAgentRuntime = HermesToolRuntime
