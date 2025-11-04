#!/usr/bin/env python3
"""Restore AutoMem from backup files.

Restores FalkorDB graph and Qdrant vectors from compressed JSON backups.
Handles ID mapping, relationship restoration, and project isolation.

Usage:
    # Basic restore
    python scripts/restore_automem.py \\
      --falkordb-backup backups/falkordb/falkordb_20250104_120000.json.gz \\
      --qdrant-backup backups/qdrant/qdrant_20250104_120000.json.gz

    # Dry run (validate only)
    python scripts/restore_automem.py --latest --dry-run

    # Restore from latest backup
    python scripts/restore_automem.py --latest

    # Selective restore
    python scripts/restore_automem.py --latest --skip-qdrant
    python scripts/restore_automem.py --latest --filter-project my-project

    # Append mode (don't clear existing data)
    python scripts/restore_automem.py --latest --append
"""

import argparse
import gzip
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from dotenv import load_dotenv
from falkordb import FalkorDB
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

# Load environment
load_dotenv()
load_dotenv(Path.home() / ".config" / "automem" / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout
)
logger = logging.getLogger("automem.restore")

# Configuration
BACKUP_DIR = Path(os.getenv("AUTOMEM_BACKUP_DIR", "./backups"))
FALKORDB_HOST = os.getenv("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.getenv("FALKORDB_PORT", "6379"))
FALKORDB_PASSWORD = os.getenv("FALKORDB_PASSWORD")
FALKORDB_GRAPH = os.getenv("FALKORDB_GRAPH", "memories")

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY") or None  # Convert empty string to None
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "memories")


class AutoMemRestore:
    """Handles restoration of AutoMem data from backups."""

    def __init__(
        self,
        falkordb_backup: Optional[Path] = None,
        qdrant_backups: Optional[List[Path]] = None,
        dry_run: bool = False,
        skip_falkordb: bool = False,
        skip_qdrant: bool = False,
        append: bool = False,
        target_project: Optional[str] = None,
        filter_project: Optional[str] = None,
    ):
        """Initialize restore operation.

        Args:
            falkordb_backup: Path to FalkorDB backup file
            qdrant_backups: List of paths to Qdrant backup files (one per collection)
            dry_run: Only validate, don't restore
            skip_falkordb: Skip FalkorDB restoration
            skip_qdrant: Skip Qdrant restoration
            append: Don't clear existing data
            target_project: Override project_id for all restored data
            filter_project: Only restore data from specific project
        """
        self.falkordb_backup = falkordb_backup
        self.qdrant_backups = qdrant_backups or []
        self.dry_run = dry_run
        self.skip_falkordb = skip_falkordb
        self.skip_qdrant = skip_qdrant
        self.append = append
        self.target_project = target_project
        self.filter_project = filter_project

        self.stats = {
            "nodes_restored": 0,
            "relationships_restored": 0,
            "points_restored": 0,
            "nodes_skipped": 0,
            "relationships_skipped": 0,
            "points_skipped": 0,
            "errors": [],
            "collections_restored": 0
        }

    def load_and_validate_backups(self) -> Tuple[Optional[Dict], Optional[Dict]]:
        """Load and validate backup files.

        Returns:
            Tuple of (falkordb_data, qdrant_data)
        """
        falkor_data = None
        qdrant_data = None

        # Load FalkorDB backup
        if self.falkordb_backup and not self.skip_falkordb:
            logger.info(f"📖 Loading FalkorDB backup: {self.falkordb_backup.name}")
            try:
                with gzip.open(self.falkordb_backup, "rt", encoding="utf-8") as f:
                    falkor_data = json.load(f)

                # Validate structure
                required_keys = ["timestamp", "graph_name", "nodes", "relationships"]
                missing = [k for k in required_keys if k not in falkor_data]
                if missing:
                    raise ValueError(f"Missing keys in FalkorDB backup: {missing}")

                logger.info(f"   ✓ Nodes: {len(falkor_data['nodes'])}")
                logger.info(f"   ✓ Relationships: {len(falkor_data['relationships'])}")
                logger.info(f"   ✓ Timestamp: {falkor_data['timestamp']}")

            except Exception as e:
                logger.error(f"❌ Failed to load FalkorDB backup: {e}")
                raise

        # Load Qdrant backup
        if self.qdrant_backup and not self.skip_qdrant:
            logger.info(f"📖 Loading Qdrant backup: {self.qdrant_backup.name}")
            try:
                with gzip.open(self.qdrant_backup, "rt", encoding="utf-8") as f:
                    qdrant_data = json.load(f)

                # Validate structure
                required_keys = ["timestamp", "collection_name", "points"]
                missing = [k for k in required_keys if k not in qdrant_data]
                if missing:
                    raise ValueError(f"Missing keys in Qdrant backup: {missing}")

                logger.info(f"   ✓ Points: {len(qdrant_data['points'])}")
                logger.info(f"   ✓ Collection: {qdrant_data['collection_name']}")
                logger.info(f"   ✓ Timestamp: {qdrant_data['timestamp']}")

            except Exception as e:
                logger.error(f"❌ Failed to load Qdrant backup: {e}")
                raise

        return falkor_data, qdrant_data

    def validate_backup_integrity(
        self,
        falkor_data: Optional[Dict],
        qdrant_data: Optional[Dict]
    ) -> List[str]:
        """Cross-validate FalkorDB and Qdrant backups.

        Returns:
            List of warning messages
        """
        warnings = []

        if not falkor_data or not qdrant_data:
            return warnings

        logger.info("🔍 Validating backup integrity...")

        # Build UUID sets
        falkor_uuids = set()
        for node in falkor_data.get("nodes", []):
            if "properties" in node and "id" in node["properties"]:
                falkor_uuids.add(node["properties"]["id"])

        qdrant_uuids = set()
        for point in qdrant_data.get("points", []):
            qdrant_uuids.add(str(point["id"]))

        # Check for missing UUIDs
        missing_in_qdrant = falkor_uuids - qdrant_uuids
        missing_in_falkor = qdrant_uuids - falkor_uuids

        if missing_in_qdrant:
            msg = f"⚠️  {len(missing_in_qdrant)} FalkorDB nodes missing from Qdrant"
            logger.warning(msg)
            warnings.append(msg)

        if missing_in_falkor:
            msg = f"⚠️  {len(missing_in_falkor)} Qdrant points missing from FalkorDB"
            logger.warning(msg)
            warnings.append(msg)

        if not missing_in_qdrant and not missing_in_falkor:
            logger.info(f"   ✓ All {len(falkor_uuids)} UUIDs match between stores")

        # Validate relationship references
        if falkor_data:
            node_internal_ids = {n["id"] for n in falkor_data.get("nodes", [])}
            invalid_rels = []

            for rel in falkor_data.get("relationships", []):
                if rel["source_id"] not in node_internal_ids:
                    invalid_rels.append(rel)
                elif rel["target_id"] not in node_internal_ids:
                    invalid_rels.append(rel)

            if invalid_rels:
                msg = f"⚠️  {len(invalid_rels)} relationships reference non-existent nodes"
                logger.warning(msg)
                warnings.append(msg)
            else:
                logger.info(f"   ✓ All {len(falkor_data.get('relationships', []))} relationships valid")

        return warnings

    def restore_falkordb(
        self,
        backup_data: Dict
    ) -> Dict[str, int]:
        """Restore FalkorDB graph from backup.

        Returns:
            Stats dict with restore counts
        """
        logger.info("📊 Restoring FalkorDB graph...")

        if self.dry_run:
            logger.info("   (dry run - no changes will be made)")
            return {"nodes": 0, "relationships": 0}

        try:
            # Connect to FalkorDB
            db = FalkorDB(
                host=FALKORDB_HOST,
                port=FALKORDB_PORT,
                password=FALKORDB_PASSWORD,
                username="default" if FALKORDB_PASSWORD else None
            )
            graph = db.select_graph(FALKORDB_GRAPH)

            # Build ID mapping
            id_mapping = self._build_id_mapping(backup_data["nodes"])

            # Clear graph if not appending
            if not self.append:
                logger.info("   🗑️  Clearing existing graph data...")
                graph.query("MATCH (n) DETACH DELETE n")
                logger.info("   ✓ Graph cleared")

            # Restore nodes
            nodes_restored = self._restore_nodes_batch(
                graph,
                backup_data["nodes"]
            )

            # Restore relationships
            rels_restored = self._restore_relationships_batch(
                graph,
                backup_data["relationships"],
                id_mapping
            )

            logger.info(f"✅ FalkorDB restore complete")
            logger.info(f"   Nodes: {nodes_restored}")
            logger.info(f"   Relationships: {rels_restored}")

            return {
                "nodes": nodes_restored,
                "relationships": rels_restored
            }

        except Exception as e:
            logger.error(f"❌ FalkorDB restore failed: {e}")
            raise

    def _build_id_mapping(self, nodes: List[Dict]) -> Dict[int, str]:
        """Build mapping from backup internal IDs to UUIDs.

        Args:
            nodes: List of node dicts from backup

        Returns:
            Dict mapping internal_id → uuid
        """
        mapping = {}
        for node in nodes:
            internal_id = node["id"]
            uuid = node["properties"].get("id")
            if uuid:
                mapping[internal_id] = uuid

        logger.info(f"   📋 Built ID mapping for {len(mapping)} nodes")
        return mapping

    def _restore_nodes_batch(
        self,
        graph: Any,
        nodes: List[Dict],
        batch_size: int = 50
    ) -> int:
        """Restore nodes in batches using MERGE.

        Args:
            graph: FalkorDB graph instance
            nodes: List of node dicts from backup
            batch_size: Number of nodes per batch

        Returns:
            Number of nodes restored
        """
        logger.info(f"   📝 Restoring {len(nodes)} nodes...")

        restored = 0
        skipped = 0

        for i in range(0, len(nodes), batch_size):
            batch = nodes[i:i + batch_size]

            for node in batch:
                try:
                    props = node["properties"]
                    labels = node["labels"]

                    # Apply project filter
                    if self.filter_project:
                        if props.get("project_id") != self.filter_project:
                            skipped += 1
                            continue

                    # Override project if specified
                    if self.target_project:
                        props["project_id"] = self.target_project

                    # Build label string
                    label_str = ":".join(labels)

                    # Build MERGE query
                    query = f"MERGE (n:{label_str} {{id: $id}}) SET n = $props"

                    # Execute
                    graph.query(query, {
                        "id": props["id"],
                        "props": props
                    })

                    restored += 1

                except Exception as e:
                    logger.warning(f"   ⚠️  Failed to restore node {props.get('id', 'unknown')}: {e}")
                    self.stats["errors"].append(f"Node {props.get('id')}: {e}")

            # Progress update
            if (i + batch_size) % 200 == 0:
                logger.info(f"   ... {min(i + batch_size, len(nodes))}/{len(nodes)} nodes processed")

        self.stats["nodes_restored"] = restored
        self.stats["nodes_skipped"] = skipped

        if skipped > 0:
            logger.info(f"   ℹ️  Skipped {skipped} nodes (filter: {self.filter_project})")

        return restored

    def _restore_relationships_batch(
        self,
        graph: Any,
        relationships: List[Dict],
        id_mapping: Dict[int, str],
        batch_size: int = 50
    ) -> int:
        """Restore relationships using UUID mapping.

        Args:
            graph: FalkorDB graph instance
            relationships: List of relationship dicts from backup
            id_mapping: Mapping from internal IDs to UUIDs
            batch_size: Number of relationships per batch

        Returns:
            Number of relationships restored
        """
        logger.info(f"   🔗 Restoring {len(relationships)} relationships...")

        restored = 0
        skipped = 0

        for i in range(0, len(relationships), batch_size):
            batch = relationships[i:i + batch_size]

            for rel in batch:
                try:
                    # Map internal IDs to UUIDs
                    source_uuid = id_mapping.get(rel["source_id"])
                    target_uuid = id_mapping.get(rel["target_id"])

                    if not source_uuid or not target_uuid:
                        skipped += 1
                        continue

                    rel_type = rel["type"]
                    props = rel.get("properties", {})

                    # Build MERGE query for relationship
                    query = f"""
                        MATCH (a {{id: $source_id}})
                        MATCH (b {{id: $target_id}})
                        MERGE (a)-[r:{rel_type}]->(b)
                        SET r = $props
                    """

                    graph.query(query, {
                        "source_id": source_uuid,
                        "target_id": target_uuid,
                        "props": props
                    })

                    restored += 1

                except Exception as e:
                    logger.warning(f"   ⚠️  Failed to restore relationship: {e}")
                    self.stats["errors"].append(f"Relationship {rel.get('type')}: {e}")

            # Progress update
            if (i + batch_size) % 200 == 0:
                logger.info(f"   ... {min(i + batch_size, len(relationships))}/{len(relationships)} relationships processed")

        self.stats["relationships_restored"] = restored
        self.stats["relationships_skipped"] = skipped

        if skipped > 0:
            logger.warning(f"   ⚠️  Skipped {skipped} relationships (missing node references)")

        return restored

    def restore_qdrant(
        self,
        backup_data: Dict
    ) -> Dict[str, int]:
        """Restore Qdrant collection from backup.

        Returns:
            Stats dict with restore counts
        """
        logger.info("🔍 Restoring Qdrant collection...")

        if self.dry_run:
            logger.info("   (dry run - no changes will be made)")
            return {"points": 0}

        try:
            # Connect to Qdrant
            client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)

            collection_name = backup_data["collection_name"]
            vector_size = backup_data["stats"].get("vector_size", 768)

            # Ensure collection exists
            self._ensure_collection(client, collection_name, vector_size)

            # Clear collection if not appending
            if not self.append:
                logger.info(f"   🗑️  Clearing collection '{collection_name}'...")
                # Delete and recreate to clear
                try:
                    client.delete_collection(collection_name)
                except Exception:
                    pass  # Collection might not exist
                self._ensure_collection(client, collection_name, vector_size)
                logger.info("   ✓ Collection cleared")

            # Restore points
            points_restored = self._restore_points_batch(
                client,
                collection_name,
                backup_data["points"]
            )

            logger.info(f"✅ Qdrant restore complete")
            logger.info(f"   Points: {points_restored}")

            return {"points": points_restored}

        except Exception as e:
            logger.error(f"❌ Qdrant restore failed: {e}")
            raise

    def _ensure_collection(
        self,
        client: QdrantClient,
        collection_name: str,
        vector_size: int
    ):
        """Create collection if it doesn't exist.

        Args:
            client: Qdrant client
            collection_name: Name of collection
            vector_size: Dimension of vectors
        """
        try:
            client.get_collection(collection_name)
            logger.info(f"   ✓ Collection '{collection_name}' exists")
        except Exception:
            logger.info(f"   📦 Creating collection '{collection_name}'...")
            client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=vector_size,
                    distance=Distance.COSINE
                )
            )
            logger.info("   ✓ Collection created")

    def _restore_points_batch(
        self,
        client: QdrantClient,
        collection_name: str,
        points: List[Dict],
        batch_size: int = 100
    ) -> int:
        """Restore Qdrant points in batches.

        Args:
            client: Qdrant client
            collection_name: Name of collection
            points: List of point dicts from backup
            batch_size: Number of points per batch

        Returns:
            Number of points restored
        """
        logger.info(f"   📝 Restoring {len(points)} points...")

        restored = 0
        skipped = 0

        for i in range(0, len(points), batch_size):
            batch = points[i:i + batch_size]

            batch_points = []
            for point in batch:
                try:
                    point_id = str(point["id"])
                    vector = point["vector"]
                    payload = point["payload"]

                    # Apply project filter
                    if self.filter_project:
                        if payload.get("project_id") != self.filter_project:
                            skipped += 1
                            continue

                    # Override project if specified
                    if self.target_project:
                        payload["project_id"] = self.target_project

                    batch_points.append(
                        PointStruct(
                            id=point_id,
                            vector=vector,
                            payload=payload
                        )
                    )

                except Exception as e:
                    logger.warning(f"   ⚠️  Failed to prepare point {point.get('id', 'unknown')}: {e}")
                    self.stats["errors"].append(f"Point {point.get('id')}: {e}")

            # Upsert batch
            if batch_points:
                try:
                    client.upsert(
                        collection_name=collection_name,
                        points=batch_points
                    )
                    restored += len(batch_points)
                except Exception as e:
                    logger.error(f"   ❌ Batch upsert failed: {e}")
                    self.stats["errors"].append(f"Batch upsert: {e}")

            # Progress update
            if (i + batch_size) % 500 == 0:
                logger.info(f"   ... {min(i + batch_size, len(points))}/{len(points)} points processed")

        self.stats["points_restored"] = restored
        self.stats["points_skipped"] = skipped

        if skipped > 0:
            logger.info(f"   ℹ️  Skipped {skipped} points (filter: {self.filter_project})")

        return restored

    def run_restore(self) -> Dict[str, Any]:
        """Execute full restore process.

        Returns:
            Results dict with stats
        """
        start_time = time.time()
        logger.info("🚀 Starting AutoMem restore")

        if self.dry_run:
            logger.info("   🔍 DRY RUN MODE - No changes will be made")

        results = {
            "success": False,
            "dry_run": self.dry_run,
            "falkordb": None,
            "qdrant": [],
            "warnings": [],
            "errors": [],
            "duration_seconds": 0
        }

        try:
            # Load and validate FalkorDB backup
            falkor_data = None
            if self.falkordb_backup and not self.skip_falkordb:
                logger.info(f"📖 Loading FalkorDB backup: {self.falkordb_backup.name}")
                try:
                    with gzip.open(self.falkordb_backup, "rt", encoding="utf-8") as f:
                        falkor_data = json.load(f)

                    required_keys = ["timestamp", "graph_name", "nodes", "relationships"]
                    missing = [k for k in required_keys if k not in falkor_data]
                    if missing:
                        raise ValueError(f"Missing keys in FalkorDB backup: {missing}")

                    logger.info(f"   ✓ Nodes: {len(falkor_data['nodes'])}")
                    logger.info(f"   ✓ Relationships: {len(falkor_data['relationships'])}")
                except Exception as e:
                    logger.error(f"❌ Failed to load FalkorDB backup: {e}")
                    raise

            # Restore FalkorDB
            if falkor_data and not self.skip_falkordb:
                falkor_stats = self.restore_falkordb(falkor_data)
                results["falkordb"] = falkor_stats

            # Restore Qdrant collections
            if self.qdrant_backups and not self.skip_qdrant:
                logger.info(f"📖 Restoring {len(self.qdrant_backups)} Qdrant collection(s)...")

                for qdrant_backup in self.qdrant_backups:
                    try:
                        logger.info(f"   Loading {qdrant_backup.name}...")
                        with gzip.open(qdrant_backup, "rt", encoding="utf-8") as f:
                            qdrant_data = json.load(f)

                        required_keys = ["timestamp", "collection_name", "points"]
                        missing = [k for k in required_keys if k not in qdrant_data]
                        if missing:
                            raise ValueError(f"Missing keys in Qdrant backup: {missing}")

                        logger.info(f"   ✓ Collection: {qdrant_data['collection_name']}, Points: {len(qdrant_data['points'])}")

                        # Restore this collection
                        qdrant_stats = self.restore_qdrant(qdrant_data)
                        results["qdrant"].append({
                            "collection": qdrant_data["collection_name"],
                            "stats": qdrant_stats
                        })
                        self.stats["collections_restored"] += 1

                    except Exception as e:
                        logger.error(f"❌ Failed to restore {qdrant_backup.name}: {e}")
                        self.stats["errors"].append(f"Qdrant {qdrant_backup.name}: {e}")

            # Report results
            duration = time.time() - start_time
            results["duration_seconds"] = round(duration, 2)
            results["errors"] = self.stats["errors"]
            results["collections_restored"] = self.stats["collections_restored"]

            if self.stats["errors"]:
                logger.warning(f"⚠️  Restore completed with {len(self.stats['errors'])} errors")
                results["success"] = False
            else:
                logger.info(f"✅ Restore completed successfully in {duration:.1f}s")
                results["success"] = True

            return results

        except Exception as e:
            logger.error(f"❌ Restore failed: {e}")
            results["errors"].append(str(e))
            results["duration_seconds"] = time.time() - start_time
            raise


def find_latest_backup(backup_dir: Path, backup_type: str) -> Optional[Path]:
    """Find the most recent backup file of specified type.

    Args:
        backup_dir: Root backup directory
        backup_type: Either 'falkordb' or 'qdrant'

    Returns:
        Path to latest backup file or None
    """
    backup_path = backup_dir / backup_type
    if not backup_path.exists():
        return None

    backups = sorted(
        backup_path.glob("*.json.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    return backups[0] if backups else None


def find_latest_qdrant_backups(backup_dir: Path) -> List[Path]:
    """Find all Qdrant backup files from the most recent backup run.

    Args:
        backup_dir: Root backup directory

    Returns:
        List of paths to Qdrant backup files from same timestamp
    """
    backup_path = backup_dir / "qdrant"
    if not backup_path.exists():
        return []

    # Find most recent backup file
    backups = sorted(
        backup_path.glob("qdrant_*.json.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    if not backups:
        return []

    # Extract timestamp from most recent file
    # Format: qdrant_{collection}_{timestamp}.json.gz or qdrant_{timestamp}.json.gz
    latest = backups[0]
    filename = latest.stem.replace(".json", "")  # Remove .json from .json.gz
    parts = filename.split("_")

    # Find timestamp (last part that looks like a timestamp)
    timestamp = parts[-1] if len(parts) > 1 else None

    if not timestamp:
        return [latest]

    # Find all backup files with this timestamp
    matching = []
    for backup_file in backup_path.glob(f"qdrant_*_{timestamp}.json.gz"):
        matching.append(backup_file)

    # Sort by collection name for consistent ordering
    matching.sort()

    logger.info(f"   Found {len(matching)} Qdrant backup file(s) from timestamp {timestamp}")
    return matching


def main():
    parser = argparse.ArgumentParser(
        description="AutoMem restore tool - restores FalkorDB and Qdrant from compressed JSON backups",
        epilog="""
Examples:
  # Restore from latest backups (all collections)
  python scripts/restore_automem.py --latest

  # Restore specific single collection only
  python scripts/restore_automem.py \\
    --falkordb-backup backups/falkordb/falkordb_20250104_120000.json.gz \\
    --qdrant-backup backups/qdrant/qdrant_memories_20250104_120000.json.gz

  # Dry run (validate only)
  python scripts/restore_automem.py --latest --dry-run

  # Selective restore
  python scripts/restore_automem.py --latest --skip-qdrant
  python scripts/restore_automem.py --latest --filter-project my-project

  # Append mode (don't clear existing data)
  python scripts/restore_automem.py --latest --append
        """
    )

    parser.add_argument(
        "--falkordb-backup",
        type=str,
        help="Path to FalkorDB backup file (.json.gz)"
    )
    parser.add_argument(
        "--qdrant-backup",
        type=str,
        help="Path to single Qdrant backup file (.json.gz) - use --latest to restore all collections"
    )
    parser.add_argument(
        "--backup-dir",
        type=str,
        default=str(BACKUP_DIR),
        help="Directory containing backup files (default: ./backups)"
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Restore from latest backup files in backup-dir"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate backups without restoring"
    )
    parser.add_argument(
        "--skip-falkordb",
        action="store_true",
        help="Skip FalkorDB restoration"
    )
    parser.add_argument(
        "--skip-qdrant",
        action="store_true",
        help="Skip Qdrant restoration"
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Don't clear existing data before restore"
    )
    parser.add_argument(
        "--target-project",
        type=str,
        help="Override project_id for all restored data"
    )
    parser.add_argument(
        "--filter-project",
        type=str,
        help="Only restore data from specific project"
    )
    parser.add_argument(
        "--falkordb-host",
        type=str,
        help=f"FalkorDB host (default: {FALKORDB_HOST})"
    )
    parser.add_argument(
        "--falkordb-port",
        type=int,
        help=f"FalkorDB port (default: {FALKORDB_PORT})"
    )
    parser.add_argument(
        "--qdrant-url",
        type=str,
        help=f"Qdrant URL (default: {QDRANT_URL})"
    )

    args = parser.parse_args()

    # Override connection settings if provided
    if args.falkordb_host:
        globals()["FALKORDB_HOST"] = args.falkordb_host
    if args.falkordb_port:
        globals()["FALKORDB_PORT"] = args.falkordb_port
    if args.qdrant_url:
        globals()["QDRANT_URL"] = args.qdrant_url

    # Determine backup files
    backup_dir = Path(args.backup_dir)

    if args.latest:
        logger.info(f"🔍 Finding latest backups in {backup_dir}")
        falkordb_backup = find_latest_backup(backup_dir, "falkordb")
        qdrant_backups = find_latest_qdrant_backups(backup_dir)

        if not falkordb_backup and not args.skip_falkordb:
            logger.error("❌ No FalkorDB backup found")
            sys.exit(1)
        if not qdrant_backups and not args.skip_qdrant:
            logger.error("❌ No Qdrant backups found")
            sys.exit(1)
    else:
        falkordb_backup = Path(args.falkordb_backup) if args.falkordb_backup else None
        # Wrap single qdrant backup in list for consistency
        qdrant_backups = [Path(args.qdrant_backup)] if args.qdrant_backup else []

        if not falkordb_backup and not args.skip_falkordb:
            logger.error("❌ --falkordb-backup required (or use --latest)")
            sys.exit(1)
        if not qdrant_backups and not args.skip_qdrant:
            logger.error("❌ --qdrant-backup required (or use --latest)")
            sys.exit(1)

    # Validate backup files exist
    if falkordb_backup and not falkordb_backup.exists():
        logger.error(f"❌ FalkorDB backup not found: {falkordb_backup}")
        sys.exit(1)
    for qdrant_backup in qdrant_backups:
        if not qdrant_backup.exists():
            logger.error(f"❌ Qdrant backup not found: {qdrant_backup}")
            sys.exit(1)

    # Run restore
    restore = AutoMemRestore(
        falkordb_backup=falkordb_backup,
        qdrant_backups=qdrant_backups,
        dry_run=args.dry_run,
        skip_falkordb=args.skip_falkordb,
        skip_qdrant=args.skip_qdrant,
        append=args.append,
        target_project=args.target_project,
        filter_project=args.filter_project,
    )

    try:
        results = restore.run_restore()
        print("\n" + "="*80)
        print(json.dumps(results, indent=2))
        print("="*80)

        sys.exit(0 if results["success"] else 1)
    except Exception as e:
        logger.error(f"Restore failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
