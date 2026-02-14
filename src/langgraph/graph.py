from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

START = "__start__"
END = "__end__"


@dataclass
class _Conditional:
    router: Callable[[dict[str, Any]], str]
    routes: dict[str, str]


class _CompiledGraph:
    def __init__(self, graph: "StateGraph") -> None:
        self.graph = graph

    def invoke(self, state: dict[str, Any]) -> dict[str, Any]:
        current = self.graph.edges[START][0]
        while current != END:
            state = self.graph.nodes[current](state)
            if current in self.graph.conditional:
                conditional = self.graph.conditional[current]
                current = conditional.routes[conditional.router(state)]
            else:
                next_nodes = self.graph.edges.get(current, [END])
                current = next_nodes[0] if next_nodes else END
        return state


class StateGraph:
    def __init__(self, _state_type: Any) -> None:
        self.nodes: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self.edges: dict[str, list[str]] = {}
        self.conditional: dict[str, _Conditional] = {}

    def add_node(self, name: str, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.nodes[name] = fn

    def add_edge(self, source: str, target: str) -> None:
        self.edges.setdefault(source, []).append(target)

    def add_conditional_edges(self, source: str, router: Callable[[dict[str, Any]], str], routes: dict[str, str]) -> None:
        self.conditional[source] = _Conditional(router=router, routes=routes)

    def compile(self) -> _CompiledGraph:
        return _CompiledGraph(self)
