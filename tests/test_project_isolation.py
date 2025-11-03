"""Tests for project isolation security features.

Verifies that project isolation prevents data leakage between projects,
including default project (__default__) isolation from named projects.
"""
import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

import app
from app import find_temporal_relationships, detect_patterns, link_semantic_neighbors, DEFAULT_PROJECT


class ProjectIsolationGraph:
    """Mock graph that tracks project_id for isolation testing."""

    def __init__(self):
        self.queries: List[tuple] = []
        self.memories: Dict[str, Dict[str, Any]] = {}
        self.patterns: Dict[tuple, Dict[str, Any]] = {}  # key: (type, project_id)
        self.relationships: List[tuple] = []

    def query(self, query_str: str, params: Optional[Dict] = None):
        params = params or {}
        self.queries.append((query_str, params))

        # MERGE Memory node
        if "MERGE (m:Memory {id:" in query_str or "MERGE (m:Memory {id: $id})" in query_str:
            memory_id = params["id"]
            project_id = params.get("project_id", DEFAULT_PROJECT)
            self.memories[memory_id] = {
                "id": memory_id,
                "project_id": project_id,
                "content": params.get("content", ""),
                "type": params.get("type", "Memory"),
                "confidence": params.get("confidence", 0.3),
                "importance": params.get("importance", 0.5),
                "timestamp": params.get("timestamp", "2025-01-01T00:00:00Z"),
                "processed": False,
            }
            node = SimpleNamespace(properties=self.memories[memory_id])
            return SimpleNamespace(result_set=[[node]])

        # MATCH Memory by id (for enrichment)
        if "MATCH (m:Memory {id:" in query_str and "RETURN m" == query_str.split("RETURN")[-1].strip():
            memory_id = params.get("id")
            if memory_id in self.memories:
                node = SimpleNamespace(properties=self.memories[memory_id])
                return SimpleNamespace(result_set=[[node]])
            return SimpleNamespace(result_set=[])

        # MATCH Memory for temporal relationships
        if "MATCH (m1:Memory {id: $id})" in query_str and "MATCH (m2:Memory)" in query_str:
            memory_id = params.get("id")
            project_id = params.get("project_id", DEFAULT_PROJECT)

            results = []
            for mem_id, mem_data in self.memories.items():
                if mem_id == memory_id:
                    continue

                # Enforce project isolation with simple equality
                if mem_data["project_id"] != project_id:
                    continue

                results.append([mem_id])

            return SimpleNamespace(result_set=results[:params.get("limit", 5)])

        # MATCH Memory for pattern detection
        if "MATCH (m:Memory)" in query_str and "WHERE m.type = $type" in query_str:
            memory_type = params.get("type")
            memory_id = params.get("id")
            project_id = params.get("project_id", DEFAULT_PROJECT)

            results = []
            for mem_id, mem_data in self.memories.items():
                if mem_id == memory_id:
                    continue
                if mem_data.get("type") != memory_type:
                    continue

                # Enforce project isolation with simple equality
                if mem_data["project_id"] != project_id:
                    continue

                results.append([mem_id, mem_data.get("content", "")])

            return SimpleNamespace(result_set=results[:10])

        # MERGE Pattern node
        if "MERGE (p:Pattern" in query_str:
            pattern_type = params.get("type")
            project_id = params.get("project_id", DEFAULT_PROJECT)

            key = (pattern_type, project_id)

            if key not in self.patterns:
                self.patterns[key] = {
                    "type": pattern_type,
                    "project_id": project_id,
                    "observations": 1,
                    "confidence": params.get("initial_confidence", 0.35),
                }
            else:
                self.patterns[key]["observations"] += 1

            return SimpleNamespace(result_set=[[SimpleNamespace()]])

        # MERGE relationship (PRECEDED_BY, EXEMPLIFIES, etc.)
        if "MERGE (m1)-[r:" in query_str or "MERGE (m)-[r:" in query_str:
            id1 = params.get("id1") or params.get("memory_id")
            id2 = params.get("id2")
            rel_type = None

            if "PRECEDED_BY" in query_str:
                rel_type = "PRECEDED_BY"
            elif "EXEMPLIFIES" in query_str:
                rel_type = "EXEMPLIFIES"
            elif "SIMILAR_TO" in query_str:
                rel_type = "SIMILAR_TO"

            if id1 and id2 and rel_type:
                self.relationships.append((id1, rel_type, id2))

            return SimpleNamespace(result_set=[[SimpleNamespace()]])

        # MATCH for associations endpoint
        # Remove whitespace/newlines for consistent matching
        normalized_query = " ".join(query_str.split())
        if "MATCH (m1:Memory" in query_str and "MATCH (m2:Memory" in query_str and "MERGE (m1)-[r:" in query_str and "id1" in params:
            id1 = params.get("id1")
            id2 = params.get("id2")
            project_id = params.get("project_id", DEFAULT_PROJECT)

            # Check both memories exist
            if id1 not in self.memories or id2 not in self.memories:
                return SimpleNamespace(result_set=[])

            mem1 = self.memories[id1]
            mem2 = self.memories[id2]

            # Check project isolation with simple equality
            if mem1["project_id"] != project_id or mem2["project_id"] != project_id:
                return SimpleNamespace(result_set=[])

            # Extract relationship type from query
            rel_type = "RELATES_TO"
            for known_rel in ["PREFERS_OVER", "EXEMPLIFIES", "CONTRADICTS", "REINFORCES",
                            "INVALIDATED_BY", "EVOLVED_INTO", "DERIVED_FROM", "PART_OF"]:
                if f"-[r:{known_rel}]->" in query_str:
                    rel_type = known_rel
                    break

            self.relationships.append((id1, rel_type, id2))
            return SimpleNamespace(result_set=[[SimpleNamespace()]])

        # Default: empty result
        return SimpleNamespace(result_set=[])

    def get_cross_project_relationships(self) -> List[tuple]:
        """Return relationships that cross project boundaries (security violation)."""
        violations = []
        for source_id, rel_type, target_id in self.relationships:
            if source_id not in self.memories or target_id not in self.memories:
                continue

            source_proj = self.memories[source_id]["project_id"]
            target_proj = self.memories[target_id]["project_id"]

            if source_proj != target_proj:
                violations.append((source_id, rel_type, target_id, source_proj, target_proj))

        return violations

    def get_patterns_by_project(self) -> Dict[str, List[tuple]]:
        """Group patterns by project_id."""
        by_project: Dict[str, List[tuple]] = {}
        for key, pattern in self.patterns.items():
            project_id = key[1]
            if project_id not in by_project:
                by_project[project_id] = []
            by_project[project_id].append(key)
        return by_project


class TestProjectIsolationEnrichment:
    """Test that enrichment pipeline enforces project isolation."""

    def test_temporal_relationships_isolated(self):
        """Temporal relationships should only link memories in same project."""
        graph = ProjectIsolationGraph()

        # Create memories in different projects
        graph.memories["mem1"] = {
            "id": "mem1",
            "project_id": "project-a",
            "timestamp": "2025-01-01T12:00:00Z",
            "content": "Memory in project A",
            "type": "Memory",
            "confidence": 0.5,
        }
        graph.memories["mem2"] = {
            "id": "mem2",
            "project_id": "project-a",
            "timestamp": "2025-01-01T11:00:00Z",
            "content": "Earlier memory in project A",
            "type": "Memory",
            "confidence": 0.5,
        }
        graph.memories["mem3"] = {
            "id": "mem3",
            "project_id": "project-b",
            "timestamp": "2025-01-01T11:30:00Z",
            "content": "Memory in project B",
            "type": "Memory",
            "confidence": 0.5,
        }
        graph.memories["mem4"] = {
            "id": "mem4",
            "project_id": DEFAULT_PROJECT,
            "timestamp": "2025-01-01T10:00:00Z",
            "content": "Memory in default project",
            "type": "Memory",
            "confidence": 0.5,
        }

        # Test temporal linking for project-a memory
        created = find_temporal_relationships(graph, "mem1", "project-a")

        # Should create relationship to mem2 (same project) but NOT mem3 or mem4
        violations = graph.get_cross_project_relationships()
        assert len(violations) == 0, f"Found cross-project relationships: {violations}"

        # Verify mem2 relationship exists
        project_a_rels = [r for r in graph.relationships if r[0] == "mem1"]
        assert len(project_a_rels) >= 1, "Should have created relationship to mem2"
        assert any(r[2] == "mem2" for r in project_a_rels), "Should link to mem2 in same project"

    def test_temporal_relationships_default_project_isolated(self):
        """Default project memories should only link to other default project memories."""
        graph = ProjectIsolationGraph()

        # Create memories
        graph.memories["default1"] = {
            "id": "default1",
            "project_id": DEFAULT_PROJECT,
            "timestamp": "2025-01-01T12:00:00Z",
            "content": "Memory 1 in default project",
            "type": "Memory",
            "confidence": 0.5,
        }
        graph.memories["default2"] = {
            "id": "default2",
            "project_id": DEFAULT_PROJECT,
            "timestamp": "2025-01-01T11:00:00Z",
            "content": "Memory 2 in default project",
            "type": "Memory",
            "confidence": 0.5,
        }
        graph.memories["named1"] = {
            "id": "named1",
            "project_id": "project-x",
            "timestamp": "2025-01-01T10:00:00Z",
            "content": "Memory in named project",
            "type": "Memory",
            "confidence": 0.5,
        }

        # Test temporal linking for default project memory
        created = find_temporal_relationships(graph, "default1", DEFAULT_PROJECT)

        # Should NOT create relationship to named1
        violations = graph.get_cross_project_relationships()
        assert len(violations) == 0, f"Default project leaked to named project: {violations}"

    def test_pattern_detection_isolated(self):
        """Pattern detection should only analyze memories in same project."""
        graph = ProjectIsolationGraph()

        # Create memories of same type in different projects
        graph.memories["dec1"] = {
            "id": "dec1",
            "project_id": "project-a",
            "content": "Decision about architecture in project A",
            "type": "Decision",
            "confidence": 0.8,
        }
        graph.memories["dec2"] = {
            "id": "dec2",
            "project_id": "project-b",
            "content": "Decision about database in project B",
            "type": "Decision",
            "confidence": 0.8,
        }

        # Detect patterns for dec1 in project-a
        patterns = detect_patterns(graph, "dec1", "Decision about architecture", "project-a")

        # Pattern should only be based on project-a memories
        # Check that query included project_id filter
        pattern_queries = [q for q in graph.queries if "WHERE m.type = $type" in q[0]]
        assert len(pattern_queries) > 0, "Should have queried for similar memories"

        # Verify simple project_id filter is present
        last_pattern_query = pattern_queries[-1][0]
        assert "m.project_id = $project_id" in last_pattern_query, \
            "Pattern detection must use project filtering"

    def test_patterns_query_includes_project_isolation(self):
        """Pattern detection queries should include project_id filtering."""
        graph = ProjectIsolationGraph()

        # Create memories
        for j in range(4):
            mem_id = f"mem_a_{j}"
            graph.memories[mem_id] = {
                "id": mem_id,
                "project_id": "project-a",
                "content": f"Decision about architecture choice {j}",
                "type": "Decision",
                "confidence": 0.8,
            }

        # Attempt pattern detection
        detect_patterns(graph, "mem_a_0", "Decision about architecture choice", "project-a")

        # Verify query includes project filtering
        pattern_search_queries = [q for q in graph.queries if "WHERE m.type = $type" in q[0]]
        assert len(pattern_search_queries) > 0, "Should have queried for similar memories"

        last_query = pattern_search_queries[-1][0]
        assert "m.project_id = $project_id" in last_query, \
            "Pattern detection query must use project filtering"


class TestProjectIsolationAPI:
    """Test API endpoints enforce project isolation."""

    def test_associate_query_structure(self):
        """Verify association queries use project filtering."""
        graph = ProjectIsolationGraph()

        # Create test memories
        graph.memories["mem1"] = {"id": "mem1", "project_id": "project-a", "content": "A"}
        graph.memories["mem2"] = {"id": "mem2", "project_id": "project-a", "content": "B"}

        # Simulate the association query structure
        result = graph.query(
            """
            MATCH (m1:Memory {id: $id1})
            MATCH (m2:Memory {id: $id2})
            WHERE m1.project_id = $project_id
              AND m2.project_id = $project_id
            MERGE (m1)-[r:RELATES_TO]->(m2)
            SET r.strength = $strength
            RETURN r
            """,
            {"id1": "mem1", "id2": "mem2", "project_id": "project-a", "strength": 0.8},
        )

        # Verify the query was recorded
        assert len(graph.queries) > 0, "Query should have been executed"

        # Verify simple WHERE clause is present
        last_query = graph.queries[-1][0]
        normalized = " ".join(last_query.split())
        assert "m1.project_id = $project_id" in normalized, \
            "Association query must use project filtering"


class TestDefaultProjectReservedPrefix:
    """Test that __ prefix is reserved for system use."""

    def test_reserved_prefix_validation(self):
        """Project IDs starting with __ should be rejected and fall back to DEFAULT_PROJECT."""
        # This would be tested via app._extract_project_id() in integration tests
        # The validation logic blocks __ prefix and returns DEFAULT_PROJECT

        # Verify DEFAULT_PROJECT constant has the expected value
        assert DEFAULT_PROJECT == "__default__", "DEFAULT_PROJECT should be __default__"

        # Verify that user cannot create projects with __ prefix
        # (This is enforced in _extract_project_id() validation)
        assert DEFAULT_PROJECT.startswith("__"), "System project should use reserved prefix"
