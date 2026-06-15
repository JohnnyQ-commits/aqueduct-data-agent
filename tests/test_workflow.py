"""工作流引擎测试。"""

from __future__ import annotations

from src.aqueduct.engine.state import WorkflowState
from src.aqueduct.engine.workflow import (
    END,
    StateGraph,
    build_dev_workflow,
    build_review_workflow,
)


class TestStateGraph:
    """StateGraph 基础测试。"""

    def test_create_graph(self):
        graph = StateGraph(state_type=dict)
        assert graph is not None

    def test_add_node(self):
        graph = StateGraph(state_type=dict)
        graph.add_node("test_node", lambda state: state)
        assert "test_node" in graph._nodes

    def test_add_edge(self):
        graph = StateGraph(state_type=dict)
        graph.add_node("a", lambda s: s)
        graph.add_node("b", lambda s: s)
        graph.add_edge("a", "b")
        assert ("a", "b") in graph._edges

    def test_set_entry_point(self):
        graph = StateGraph(state_type=dict)
        graph.add_node("start", lambda s: s)
        graph.set_entry_point("start")
        assert graph._entry_point == "start"

    def test_compile(self):
        graph = StateGraph(state_type=dict)
        graph.add_node("a", lambda s: s)
        graph.add_node("b", lambda s: s)
        graph.set_entry_point("a")
        graph.add_edge("a", "b")
        compiled = graph.compile()
        assert compiled is not None

    def test_compile_without_entry_point_raises(self):
        from src.aqueduct.exceptions import WorkflowError

        graph = StateGraph(state_type=dict)
        graph.add_node("a", lambda s: s)
        try:
            graph.compile()
            assert False, "Should have raised WorkflowError"
        except WorkflowError:
            pass


class TestCompiledWorkflowExecution:
    """CompiledWorkflow 执行测试。"""

    def _make_state(self) -> WorkflowState:
        return {"requirement": "", "mode": "dev", "errors": [], "artifacts": []}

    def test_linear_execution(self):
        """线性 DAG：a → b → c，按顺序执行。"""
        order = []

        def node_a(state):
            order.append("a")
            return state

        def node_b(state):
            order.append("b")
            return state

        def node_c(state):
            order.append("c")
            return state

        graph = StateGraph(WorkflowState)
        graph.add_node("a", node_a)
        graph.add_node("b", node_b)
        graph.add_node("c", node_c)
        graph.set_entry_point("a")
        graph.add_edge("a", "b")
        graph.add_edge("b", "c")
        graph.add_edge("c", END)

        compiled = graph.compile()
        compiled.invoke(self._make_state())
        assert order == ["a", "b", "c"]

    def test_state_propagation(self):
        """节点修改的状态能传递给下游。"""

        def node_a(state):
            state["requirement_summary"] = "parsed"
            return state

        def node_b(state):
            assert state.get("requirement_summary") == "parsed"
            state["design_scheme"] = "designed"
            return state

        graph = StateGraph(WorkflowState)
        graph.add_node("a", node_a)
        graph.add_node("b", node_b)
        graph.set_entry_point("a")
        graph.add_edge("a", "b")
        graph.add_edge("b", END)

        compiled = graph.compile()
        final = compiled.invoke(self._make_state())
        assert final["requirement_summary"] == "parsed"
        assert final["design_scheme"] == "designed"

    def test_halt_on_fatal_error(self):
        """致命错误时工作流终止，后续节点不执行。"""
        executed = []

        def node_a(state):
            executed.append("a")
            state.setdefault("errors", []).append("终止：输入无效")
            return state

        def node_b(state):
            executed.append("b")
            return state

        graph = StateGraph(WorkflowState)
        graph.add_node("a", node_a)
        graph.add_node("b", node_b)
        graph.set_entry_point("a")
        graph.add_edge("a", "b")
        graph.add_edge("b", END)

        compiled = graph.compile()
        compiled.invoke(self._make_state())
        assert executed == ["a"]  # b 不应执行

    def test_cycle_detection(self):
        """环不会导致无限循环，未执行的节点被跳过。"""
        graph = StateGraph(WorkflowState)
        graph.add_node("a", lambda s: s)
        graph.add_node("b", lambda s: s)
        graph.set_entry_point("a")
        graph.add_edge("a", "b")
        graph.add_edge("b", "a")  # 形成环

        compiled = graph.compile()
        # 不应无限循环
        final = compiled.invoke(self._make_state())
        # 两个节点都应被访问（入度都不为 0 但 a 是入口）
        # 关键是测试不 hang


class TestBuildWorkflows:
    """工作流构建测试。"""

    def test_build_dev_workflow(self):
        workflow = build_dev_workflow()
        assert workflow is not None

    def test_build_review_workflow(self):
        workflow = build_review_workflow()
        assert workflow is not None


class TestWorkflowState:
    """WorkflowState 类型测试。"""

    def test_create_state(self):
        state: WorkflowState = {
            "requirement": "test requirement",
            "mode": "dev",
            "metadata": {"requirement_name": "test"},
            "errors": [],
            "artifacts": [],
        }
        assert state["requirement"] == "test requirement"
        assert state["mode"] == "dev"

    def test_state_optional_fields(self):
        state: WorkflowState = {
            "requirement": "",
            "mode": "dev",
            "metadata": {},
            "errors": [],
            "artifacts": [],
        }
        # Optional fields can be absent
        assert state.get("requirement_summary") is None
        assert state.get("design_scheme") is None
