from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from jl_agent.control.control_plane import HermesInvocationProjection
from jl_agent.control.execution_adapter import (
    ExecutionErrorCategory,
    HermesAIAgentRuntime,
    HermesExecutionAdapter,
    HermesExecutionStatus,
    HermesRuntimeRequest,
    HermesRuntimeResult,
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

    def test_aia_agent_runtime_uses_hermes_exact_tool_dispatch(self) -> None:
        constructor: dict[str, object] = {}
        dispatched: dict[str, object] = {}

        class FakeAgent:
            def __init__(self, **kwargs: object) -> None:
                constructor.update(kwargs)

        def invoke_tool(
            agent: object, name: str, arguments: dict[str, object], task_id: str
        ) -> str:
            dispatched.update(
                agent=agent, name=name, arguments=arguments, task_id=task_id
            )
            return '{"content":"safe result"}'

        runtime = HermesAIAgentRuntime("/pinned/hermes", agent_loader=lambda: FakeAgent)
        request = HermesRuntimeRequest(
            provider="provider-a",
            model="model-a",
            fallback_routes=(("provider-b", "model-b"),),
            enabled_toolsets=("file",),
            allowed_tool="read_file",
            session_id="session-1",
            task_id="request-1",
            arguments_json='{ "path": "README.md" }',
        )
        with patch(
            "jl_agent.control.execution_adapter.importlib.import_module",
            return_value=SimpleNamespace(invoke_tool=invoke_tool),
        ):
            result = runtime.run(request)

        self.assertEqual(result.status, HermesExecutionStatus.COMPLETED)
        self.assertEqual(constructor["provider"], "provider-a")
        self.assertEqual(
            constructor["fallback_model"],
            [{"provider": "provider-b", "model": "model-b"}],
        )
        self.assertEqual(dispatched["name"], "read_file")
        self.assertEqual(dispatched["arguments"], {"path": "README.md"})


if __name__ == "__main__":
    unittest.main()
