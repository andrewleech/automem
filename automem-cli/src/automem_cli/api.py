"""HTTP client for AutoMem API"""

from typing import Any, Optional

import httpx

from .config import Config


class AutoMemClient:
    """Client for interacting with AutoMem API"""

    def __init__(self, config: Config):
        self.config = config
        headers = {}

        if config.api_token:
            headers["Authorization"] = f"Bearer {config.api_token}"

        headers["X-Project-ID"] = config.project_id

        self.client = httpx.Client(
            base_url=config.endpoint,
            headers=headers,
            timeout=30.0,
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.client.close()

    def close(self):
        """Close the HTTP client"""
        self.client.close()

    def store_memory(
        self,
        content: str,
        memory_type: Optional[str] = None,
        importance: Optional[float] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Store a memory

        Args:
            content: Memory content
            memory_type: Type (decision, insight, pattern, context, preference)
            importance: Importance score 0.0-1.0
            tags: List of tags
            metadata: Additional metadata

        Returns:
            Memory object with id, content, timestamp, etc.
        """
        payload = {"content": content}

        if memory_type:
            # Capitalize memory type for server (expects: Context, Decision, etc.)
            payload["type"] = memory_type.capitalize()
        if importance is not None:
            payload["importance"] = importance
        if tags:
            payload["tags"] = tags
        if metadata:
            payload["metadata"] = metadata

        response = self.client.post("/memory", json=payload)
        response.raise_for_status()
        return response.json()

    def recall_memories(
        self,
        query: str = "",
        limit: int = 10,
        importance_min: Optional[float] = None,
        importance_max: Optional[float] = None,
        tags: Optional[list[str]] = None,
        memory_type: Optional[str] = None,
        time_query: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        tag_mode: Optional[str] = None,
        tag_match: Optional[str] = None,
    ) -> dict[str, Any]:
        """Recall memories

        Args:
            query: Search query (text-based, performs semantic search)
            limit: Maximum results to return
            importance_min: Minimum importance filter
            importance_max: Maximum importance filter
            tags: Filter by tags
            memory_type: Filter by type
            time_query: Natural language time phrase (e.g., 'today', 'last week')
            start: ISO timestamp lower bound (e.g., '2025-09-01T00:00:00Z')
            end: ISO timestamp upper bound (e.g., '2025-09-30T23:59:59Z')
            tag_mode: 'any' (default) or 'all' - how to match multiple tags
            tag_match: 'prefix' (default) or 'exact' - tag matching strategy

        Returns:
            Dict with 'results' list and metadata
        """
        params = {"limit": limit}

        if query:
            params["query"] = query
        if importance_min is not None:
            params["importance_min"] = importance_min
        if importance_max is not None:
            params["importance_max"] = importance_max
        if tags:
            params["tags"] = ",".join(tags)
        if memory_type:
            params["type"] = memory_type
        if time_query:
            params["time_query"] = time_query
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        if tag_mode:
            params["tag_mode"] = tag_mode
        if tag_match:
            params["tag_match"] = tag_match

        # Add X-Max-Results header if limit > 50 to override API default
        headers = {}
        if limit > 50:
            headers["X-Max-Results"] = str(limit)

        response = self.client.get("/recall", params=params, headers=headers)
        response.raise_for_status()
        return response.json()

    def get_memory(self, memory_id: str) -> dict[str, Any]:
        """Get a specific memory by ID

        Args:
            memory_id: Memory UUID

        Returns:
            Memory object
        """
        response = self.client.get(f"/memory/{memory_id}")
        response.raise_for_status()
        return response.json()

    def create_association(
        self,
        memory1_id: str,
        memory2_id: str,
        relation_type: str = "RELATES_TO",
        strength: float = 0.5,
        **properties,
    ) -> dict[str, Any]:
        """Create relationship between memories

        Args:
            memory1_id: Source memory ID
            memory2_id: Target memory ID
            relation_type: Relationship type
            strength: Relationship strength 0.0-1.0
            **properties: Additional relationship properties

        Returns:
            Response with status and details
        """
        payload = {
            "memory1_id": memory1_id,
            "memory2_id": memory2_id,
            "type": relation_type,
            "strength": strength,
            **properties,
        }

        response = self.client.post("/associate", json=payload)
        response.raise_for_status()
        return response.json()

    def consolidate(
        self, mode: str = "decay", dry_run: bool = False
    ) -> dict[str, Any]:
        """Trigger memory consolidation

        Args:
            mode: Consolidation mode (decay, creative, cluster, forget, full)
            dry_run: Preview changes without applying

        Returns:
            Consolidation results
        """
        payload = {"mode": mode, "dry_run": dry_run}

        response = self.client.post("/consolidate", json=payload)
        response.raise_for_status()
        return response.json()

    def get_consolidation_status(self) -> dict[str, Any]:
        """Get consolidation scheduler status

        Returns:
            Status dict with next run times and history
        """
        response = self.client.get("/consolidate/status")
        response.raise_for_status()
        return response.json()

    def health_check(self) -> dict[str, Any]:
        """Check API health

        Returns:
            Health status dict with optional project stats
        """
        # Health endpoint will include project stats if X-Project-ID header is set
        # (it's already set in client headers from __init__)
        response = self.client.get("/health")
        response.raise_for_status()
        return response.json()

    def startup_recall(self) -> dict[str, Any]:
        """Recall critical lessons and recent memories at session startup

        Returns:
            Dict with critical_lessons, system_rules, recent_memories lists
        """
        response = self.client.get("/startup-recall")
        response.raise_for_status()
        return response.json()

    def list_projects(self) -> dict[str, Any]:
        """List all projects with memory counts

        Returns:
            Dict with 'projects' list containing project info
        """
        response = self.client.get("/projects")
        response.raise_for_status()
        return response.json()

    def clear_project(self, project_id: str, admin_token: str) -> dict[str, Any]:
        """Clear all data for a specific project

        Args:
            project_id: Project identifier to clear
            admin_token: Admin API token for authorization

        Returns:
            Dict with deletion stats
        """
        response = self.client.delete(
            f"/projects/{project_id}",
            params={"confirm": "yes"},
            headers={"X-Admin-Token": admin_token}
        )
        response.raise_for_status()
        return response.json()
