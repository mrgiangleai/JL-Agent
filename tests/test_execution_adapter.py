from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from jl_agent.control.control_plane import HermesInvocationProjection
from jl_agent.control.execution_adapter import (
    ExecutionErrorCategory,
    HermesExecutionAdapter,
    HermesExecutionStatus,
    HermesRuntimeRequest,
    HermesRuntimeResult,
    HermesToolRuntime,
    _AuthorizedHermesCommand,
)


class FakeRuntime:
    def __init__(self, result: HermesRuntimeResult) -> None:
        self.result = result
        self.requests: list[HermesRuntimeRequest] = []

    def run(self, request: HermesRuntimeRequest) -> HermesRuntimeResult:
        self.requests.append(request)
        return self.result


def projection(**changes: object) -> HermesInvocationProjection:
    values: dict[str, object] = {
        "capability_id": "core.hermes.files",
        "capability_version": "0.21.2",
        "entrypoint_kind": "hermes-tool",
        "entrypoint_address": "file",
        "provider": "local-provider",
        "model": "local-model",
        "route_candidate_id": "local",
        "fallback_candidate_ids": ("fallback",),
        "fallback_routes": (("fallback-provider", "fallback-model"),),
        "action": "read_file",
        "arguments_json": '{"path":"docs/ARCHITECTURE.md"}',
        "caller_id": "native-app",
        "session_id": "session-1",
        "binding_fingerprint": "a" * 64,
    }
    values.update(changes)
    return HermesInvocationProjection(**values)  # type: ignore[arg-type]


class HermesExecutionAdapterTests(unittest.TestCase):
    def test_gate_command_maps_exact_route_tool_session_and_arguments(self) -> None:
        runtime = FakeRuntime(
            HermesRuntimeResult(HermesExecutionStatus.COMPLETED, "ok")
        )
        adapter = HermesExecutionAdapter(runtime)

        result = adapter._execute(adapter._issue_command(projection(), "request-1"))

        self.assertEqual(result.status, HermesExecutionStatus.COMPLETED)
        request = runtime.requests[0]
        self.assertEqual(request.provider, "local-provider")
        self.assertEqual(request.model, "local-model")
        self.assertEqual(request.enabled_toolsets, ("file",))
        self.assertEqual(request.allowed_tool, "read_file")
        self.assertEqual(request.session_id, "session-1")
        self.assertEqual(
            json.loads(request.arguments_json),
            {"path": "docs/ARCHITECTURE.md"},
        )

    def test_computer_use_maps_to_the_single_hermes_tool(self) -> None:
        runtime = FakeRuntime(
            HermesRuntimeResult(HermesExecutionStatus.COMPLETED, "captured")
        )
        adapter = HermesExecutionAdapter(runtime)
        projected = projection(
            capability_id="core.hermes.computer-use",
            entrypoint_address="computer_use",
            action="computer_use",
            arguments_json='{"action":"capture","mode":"ax"}',
        )

        result = adapter._execute(adapter._issue_command(projected, "request-cu"))

        self.assertEqual(result.status, HermesExecutionStatus.COMPLETED)
        self.assertEqual(runtime.requests[0].enabled_toolsets, ("computer_use",))
        self.assertEqual(runtime.requests[0].allowed_tool, "computer_use")

    def test_forged_or_out_of_toolset_command_fails_closed(self) -> None:
        runtime = FakeRuntime(HermesRuntimeResult(HermesExecutionStatus.COMPLETED))
        adapter = HermesExecutionAdapter(runtime)
        forged = _AuthorizedHermesCommand(projection(), "request-1", object())
        self.assertEqual(
            adapter._execute(forged).error_category,
            ExecutionErrorCategory.POLICY_DENIED,
        )

        invalid = projection(action="terminal")
        result = adapter._execute(adapter._issue_command(invalid, "request-2"))
        self.assertEqual(result.error_category, ExecutionErrorCategory.INVALID_REQUEST)
        self.assertEqual(runtime.requests, [])

        computer = projection(
            capability_id="core.hermes.computer-use",
            entrypoint_address="computer_use",
            action="computer_use",
            arguments_json='{"action":"click","app":"TextEdit"}',
        )
        forged_computer = _AuthorizedHermesCommand(
            computer, "request-computer", object()
        )
        self.assertEqual(
            adapter._execute(forged_computer).error_category,
            ExecutionErrorCategory.POLICY_DENIED,
        )
        self.assertEqual(runtime.requests, [])

    def test_tool_runtime_uses_exact_middleware_preserving_dispatch(self) -> None:
        dispatched: dict[str, object] = {}

        def dispatch(
            name: str,
            arguments: dict[str, object],
            task_id: str,
            **kwargs: object,
        ) -> str:
            dispatched.update(
                name=name, arguments=arguments, task_id=task_id, **kwargs
            )
            return '{"content":"safe result"}'

        runtime = HermesToolRuntime(
            "/pinned/hermes", dispatcher_loader=lambda: dispatch
        )
        request = HermesRuntimeRequest(
            provider="native-validation",
            model="deterministic-boundary",
            fallback_routes=(("provider-b", "model-b"),),
            enabled_toolsets=("file",),
            allowed_tool="read_file",
            session_id="session-1",
            task_id="request-1",
            arguments_json='{ "path": "README.md" }',
        )
        result = runtime.run(request)

        self.assertEqual(result.status, HermesExecutionStatus.COMPLETED)
        self.assertEqual(dispatched["name"], "read_file")
        self.assertEqual(dispatched["arguments"], {"path": "README.md"})
        self.assertEqual(dispatched["task_id"], "request-1")
        self.assertEqual(dispatched["session_id"], "session-1")
        self.assertEqual(dispatched["enabled_tools"], ["read_file"])
        self.assertEqual(dispatched["enabled_toolsets"], ["file"])
        self.assertEqual(dispatched["disabled_toolsets"], [])

    def test_tool_runtime_loader_uses_public_dispatcher_without_agent_import(
        self,
    ) -> None:
        dispatcher = object()
        runtime = HermesToolRuntime("/pinned/hermes")

        with patch(
            "jl_agent.control.execution_adapter.importlib.import_module",
            return_value=SimpleNamespace(handle_function_call=dispatcher),
        ) as import_module:
            loaded = runtime._load_dispatcher()

        self.assertIs(loaded, dispatcher)
        import_module.assert_called_once_with("model_tools")

    def test_tool_runtime_preserves_hermes_rejection_and_failure(self) -> None:
        request = HermesRuntimeRequest(
            provider="unused-provider",
            model="unused-model",
            fallback_routes=(),
            enabled_toolsets=("computer_use",),
            allowed_tool="computer_use",
            session_id="session-1",
            task_id="request-1",
            arguments_json='{"action":"capture","mode":"ax"}',
        )
        denied = HermesToolRuntime(
            "/pinned/hermes",
            dispatcher_loader=lambda: (
                lambda *_args, **_kwargs: '{"error":"blocked","status":"blocked"}'
            ),
        ).run(request)
        failed = HermesToolRuntime(
            "/pinned/hermes",
            dispatcher_loader=lambda: (
                lambda *_args, **_kwargs: '{"error":"driver failed"}'
            ),
        ).run(request)

        self.assertEqual(denied.status, HermesExecutionStatus.DENIED)
        self.assertEqual(denied.error_category, ExecutionErrorCategory.UPSTREAM_DENIED)
        self.assertEqual(failed.status, HermesExecutionStatus.FAILED)
        self.assertEqual(failed.error_category, ExecutionErrorCategory.PROVIDER_ERROR)


if __name__ == "__main__":
    unittest.main()
