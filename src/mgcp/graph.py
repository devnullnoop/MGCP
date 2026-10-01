"""Graph operations for MGCP lesson relationships using NetworkX."""

import hashlib
from collections import Counter

import networkx as nx

from .models import Lesson


class LessonGraph:
    """Manages lesson relationships as a directed graph."""

    def __init__(self):
        self.graph = nx.DiGraph()

    def add_lesson(self, lesson: Lesson) -> None:
        """Add a lesson node to the graph."""
        self.graph.add_node(
            lesson.id,
            trigger=lesson.trigger,
            action=lesson.action,
            tags=lesson.tags,
            usage_count=lesson.usage_count,
        )

        # Add parent edge
        if lesson.parent_id:
            self.graph.add_edge(lesson.parent_id, lesson.id, relation="parent")

        # Add typed relationship edges (new system)
        for rel in lesson.relationships:
            self.graph.add_edge(
                lesson.id,
                rel.target,
                relation=rel.type,
                weight=rel.weight,
                context=rel.context,
                bidirectional=rel.bidirectional,
            )

    def remove_graph_lesson(self, lesson_id: str) -> None:
        """Remove a lesson from the graph (NetworkX)."""
        if lesson_id in self.graph:
            self.graph.remove_node(lesson_id)

    def get_children(self, lesson_id: str) -> list[str]:
        """Get direct children of a lesson."""
        children = []
        for _, target, data in self.graph.out_edges(lesson_id, data=True):
            if data.get("relation") == "parent":
                children.append(target)
        return children

    def get_parent(self, lesson_id: str) -> str | None:
        """Get parent of a lesson."""
        for source, _, data in self.graph.in_edges(lesson_id, data=True):
            if data.get("relation") == "parent":
                return source
        return None

    def get_related(self, lesson_id: str, relation_type: str | None = None) -> list[str]:
        """Get related lessons (bidirectional).

        Args:
            lesson_id: The lesson to get relationships for
            relation_type: Optional filter for specific relationship type.
                          If None, returns all non-parent relationships.
        """
        related = set()
        # Outgoing edges
        for _, target, data in self.graph.out_edges(lesson_id, data=True):
            rel = data.get("relation")
            if rel != "parent":  # Exclude parent relationships
                if relation_type is None or rel == relation_type:
                    related.add(target)
        # Incoming edges (for bidirectional relationships)
        for source, _, data in self.graph.in_edges(lesson_id, data=True):
            rel = data.get("relation")
            if rel != "parent" and data.get("bidirectional", True):
                if relation_type is None or rel == relation_type:
                    related.add(source)
        # Sorted, not set order: set iteration over strings varies between
        # processes, and spider() turns neighbour order into which nodes fall
        # outside its depth limit. Callers get the same answer every run.
        return sorted(related)

    def spider(
        self,
        start_id: str,
        depth: int = 2,
        include_related: bool = True,
    ) -> tuple[list[str], list[list[str]]]:
        """
        Traverse the graph from a starting lesson.

        Returns:
            (visited_ids, paths) - All visited nodes and the paths taken
        """
        if start_id not in self.graph:
            return [], []

        # Best depth reached per node, not a plain visited set. A node first
        # reached at depth 2 used to block a later depth-1 path to it, so its
        # children — well inside the limit — were never traversed at all.
        # Re-expand whenever we arrive more cheaply.
        best: dict[str, int] = {}
        paths = []

        def traverse(node_id: str, current_path: list[str], current_depth: int):
            if current_depth > depth:
                return
            if best.get(node_id, current_depth + 1) <= current_depth:
                return

            best[node_id] = current_depth
            current_path = current_path + [node_id]

            if len(current_path) > 1:
                paths.append(current_path.copy())

            # Traverse children (parent relation)
            for child_id in self.get_children(node_id):
                traverse(child_id, current_path, current_depth + 1)

            # Traverse related if enabled
            if include_related:
                for related_id in self.get_related(node_id):
                    traverse(related_id, current_path, current_depth + 1)

        traverse(start_id, [], 0)
        return list(best), paths

    def get_ancestors(self, lesson_id: str) -> list[str]:
        """Get all ancestors (parents, grandparents, etc.) up to root."""
        ancestors = []
        # mgcp-import writes parent_id straight from the file without the
        # existence check the add_lesson tool does, so an imported pair that
        # parents each other reaches here as a cycle. get_statistics and
        # to_dict walk every node's ancestry, so an unguarded walk hangs the
        # whole export rather than one lesson.
        seen = {lesson_id}
        current = lesson_id
        while True:
            parent = self.get_parent(current)
            if parent is None or parent in seen:
                break
            ancestors.append(parent)
            seen.add(parent)
            current = parent
        return ancestors

    def get_roots(self) -> list[str]:
        """Get all root lessons (no parent)."""
        roots = []
        for node in self.graph.nodes():
            if self.get_parent(node) is None:
                roots.append(node)
        return roots

    def get_hierarchy_depth(self, lesson_id: str) -> int:
        """Get depth of a lesson in the hierarchy (0 for root)."""
        return len(self.get_ancestors(lesson_id))

    def get_statistics(self) -> dict:
        """Get graph statistics."""
        return {
            "total_nodes": self.graph.number_of_nodes(),
            "total_edges": self.graph.number_of_edges(),
            "root_count": len(self.get_roots()),
            "max_depth": max(
                (self.get_hierarchy_depth(n) for n in self.graph.nodes()),
                default=0,
            ),
            "connected_components": nx.number_weakly_connected_components(self.graph),
        }

    def to_dict(self) -> dict:
        """Export graph as dictionary for visualization."""
        nodes = []
        for node_id in self.graph.nodes():
            data = self.graph.nodes[node_id]
            nodes.append({
                "id": node_id,
                "trigger": data.get("trigger", ""),
                "action": data.get("action", ""),
                "tags": data.get("tags", []),
                "usage_count": data.get("usage_count", 0),
                "depth": self.get_hierarchy_depth(node_id),
            })

        links = []
        for source, target, data in self.graph.edges(data=True):
            links.append({
                "source": source,
                "target": target,
                "relation": data.get("relation", "unknown"),
                "weight": data.get("weight", 0.5),
                "context": data.get("context", []),
                "bidirectional": data.get("bidirectional", True),
            })

        return {"nodes": nodes, "links": links}

    def detect_communities(
        self, resolution: float = 1.0, seed: int = 42
    ) -> list[dict]:
        """Detect natural topic clusters using Louvain community detection.

        Args:
            resolution: Controls granularity. Higher = more communities. Default 1.0.
            seed: Random seed for deterministic results.

        Returns:
            List of community dicts with: community_id, members, size,
            aggregate_tags, internal_edges, external_edges, density, top_members.
        """
        if self.graph.number_of_nodes() == 0:
            return []

        # Louvain requires an undirected graph
        undirected = self.graph.to_undirected()

        # Run Louvain community detection
        communities = nx.algorithms.community.louvain_communities(
            undirected, resolution=resolution, seed=seed
        )

        results = []
        for members_set in communities:
            members = sorted(members_set)

            # Deterministic community ID from sorted members
            community_id = hashlib.sha256(
                ",".join(members).encode()
            ).hexdigest()[:12]

            # Aggregate tags from member nodes
            tag_counter: Counter = Counter()
            top_members = []
            for member_id in members:
                node_data = self.graph.nodes.get(member_id, {})
                for tag in node_data.get("tags", []):
                    tag_counter[tag] += 1
                top_members.append((
                    member_id,
                    node_data.get("usage_count", 0),
                ))

            # Sort by usage_count descending, take top 5
            top_members.sort(key=lambda x: x[1], reverse=True)
            top_members = [m[0] for m in top_members[:5]]

            # Count internal vs external edges
            internal_edges = 0
            external_edges = 0
            for u, v in self.graph.edges():
                u_in = u in members_set
                v_in = v in members_set
                if u_in and v_in:
                    internal_edges += 1
                elif u_in or v_in:
                    external_edges += 1

            # Calculate density: internal_edges / max_possible_edges
            n = len(members)
            max_possible = n * (n - 1) if n > 1 else 1  # directed graph
            density = internal_edges / max_possible if max_possible > 0 else 0.0

            results.append({
                "community_id": community_id,
                "members": members,
                "size": n,
                "aggregate_tags": dict(tag_counter.most_common(10)),
                "internal_edges": internal_edges,
                "external_edges": external_edges,
                "density": round(density, 3),
                "top_members": top_members,
            })

        # Sort by size descending
        results.sort(key=lambda c: c["size"], reverse=True)
        return results

    def load_from_lessons(self, lessons: list[Lesson]) -> None:
        """Load graph from a list of lessons."""
        self.graph.clear()
        for lesson in lessons:
            self.add_lesson(lesson)
