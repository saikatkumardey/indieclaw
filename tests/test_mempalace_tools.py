import json
import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp()
os.environ["MEMPALACE_PALACE_PATH"] = os.path.join(_tmp, "palace")

def _load_tool(name):
    import importlib.util
    from pathlib import Path
    path = Path.home() / ".indieclaw" / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMempalaceMemory:
    @pytest.fixture(scope="class")
    def tool(self):
        return _load_tool("mempalace_memory")

    def test_schema_has_required_fields(self, tool):
        schema = tool.SCHEMA
        assert schema["type"] == "function"
        fn = schema["function"]
        assert "name" in fn
        assert "description" in fn
        assert "parameters" in fn
        params = fn["parameters"]
        assert params["type"] == "object"
        assert "properties" in params
        props = params["properties"]
        assert "action" in props
        assert "wing" in props
        assert "room" in props
        assert "content" in props

    def test_store_and_recall(self, tool):
        # Store a memory
        store_result = tool.execute(
            action="store",
            wing="test_wing",
            room="test_room",
            content="the sky is blue",
        )
        store_data = json.loads(store_result)
        assert store_data["success"] is True
        assert "drawer_id" in store_data
        assert store_data["wing"] == "test_wing"
        assert store_data["room"] == "test_room"

        # Recall by query
        recall_result = tool.execute(
            action="recall",
            query="sky color",
        )
        recall_data = json.loads(recall_result)
        assert "results" in recall_data
        texts = [r["text"] for r in recall_data["results"]]
        assert any("blue" in t for t in texts), f"Expected 'blue' in results: {texts}"

    def test_store_requires_content(self, tool):
        result = tool.execute(action="store", wing="w", room="r")
        assert "error" in result.lower() or "Error" in result

    def test_status(self, tool):
        result = tool.execute(action="status")
        data = json.loads(result)
        assert "total_drawers" in data
        assert isinstance(data["total_drawers"], int)
        assert "palace_path" in data

    def test_unknown_action(self, tool):
        result = tool.execute(action="foobar")
        assert "error" in result.lower() or "unknown" in result.lower()

    def test_store_uses_content_for_recall(self, tool):
        """recall can accept query via 'content' param as fallback"""
        # Store something unique
        tool.execute(
            action="store",
            wing="test_wing",
            room="colors",
            content="the ocean is deep and wide",
        )
        # Recall using 'content' param instead of 'query'
        recall_result = tool.execute(
            action="recall",
            content="ocean depth",
        )
        recall_data = json.loads(recall_result)
        assert "results" in recall_data

    def test_consolidate_returns_compressed(self, tool):
        # Store a couple memories in a wing
        tool.execute(
            action="store",
            wing="compress_test",
            room="facts",
            content="fact one: water is wet",
        )
        tool.execute(
            action="store",
            wing="compress_test",
            room="facts",
            content="fact two: fire is hot",
        )
        result = tool.execute(action="consolidate", wing="compress_test")
        data = json.loads(result)
        assert "original_count" in data
        assert "compressed" in data
        assert data["original_count"] >= 2


class TestMempalaceKnowledge:
    def setup_method(self):
        self.tool = _load_tool("mempalace_knowledge")

    def test_schema_has_required_fields(self):
        schema = self.tool.SCHEMA
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] == "mempalace_knowledge"
        assert "description" in fn
        params = fn["parameters"]
        assert params["type"] == "object"
        assert "action" in params["properties"]
        assert "action" in params["required"]

    def test_add_and_query(self):
        add_result = self.tool.execute(
            action="add",
            subject="indieclaw_test_subject",
            predicate="uses",
            object="mempalace_test_obj",
        )
        data = json.loads(add_result)
        assert data["success"] is True
        assert "triple_id" in data
        assert "fact" in data

        query_result = self.tool.execute(action="query", entity="indieclaw_test_subject")
        qdata = json.loads(query_result)
        assert qdata["entity"] == "indieclaw_test_subject"
        assert qdata["count"] > 0
        assert isinstance(qdata["facts"], list)

    def test_invalidate(self):
        self.tool.execute(
            action="add",
            subject="donna_test_invalidate",
            predicate="uses",
            object="icm_test",
        )
        result = self.tool.execute(
            action="invalidate",
            subject="donna_test_invalidate",
            predicate="uses",
            object="icm_test",
        )
        data = json.loads(result)
        assert data["success"] is True
        assert "fact" in data
        assert "ended" in data

    def test_timeline(self):
        self.tool.execute(
            action="add",
            subject="donna_test_timeline",
            predicate="learned",
            object="mempalace_test_tl",
        )
        result = self.tool.execute(action="timeline", entity="donna_test_timeline")
        data = json.loads(result)
        assert "timeline" in data
        assert "count" in data

    def test_add_requires_subject(self):
        result = self.tool.execute(action="add")
        assert "error" in result.lower() or "Error" in result

    def test_unknown_action(self):
        result = self.tool.execute(action="frobnicate")
        assert "error" in result.lower() or "unknown" in result.lower()


class TestMempalaceDiary:
    def setup_method(self):
        self.tool = _load_tool("mempalace_diary")

    def test_schema_has_required_fields(self):
        schema = self.tool.SCHEMA
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] == "mempalace_diary"
        assert "description" in fn
        params = fn["parameters"]
        assert params["type"] == "object"
        props = params["properties"]
        assert "action" in props
        assert "entry" in props
        assert "topic" in props
        assert "last_n" in props
        assert "action" in params["required"]

    def test_write_and_read(self):
        # write an entry
        result = self.tool.execute(action="write", entry="Helped with mempalace integration today.", topic="dev")
        data = json.loads(result)
        assert data["success"] is True
        assert "entry_id" in data
        assert data["topic"] == "dev"
        assert "timestamp" in data

        # read entries back
        read_result = self.tool.execute(action="read", last_n="5")
        read_data = json.loads(read_result)
        assert "entries" in read_data
        assert read_data["total"] >= 1
        contents = [e["content"] for e in read_data["entries"]]
        assert any("mempalace" in c for c in contents)

    def test_write_requires_entry(self):
        result = self.tool.execute(action="write", entry="")
        assert "error" in result.lower() or "Error" in result

    def test_unknown_action(self):
        result = self.tool.execute(action="fly")
        assert "error" in result.lower() or "unknown" in result.lower()
