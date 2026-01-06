"""Graph operations for MGCP lesson relationships using NetworkX."""

from typing import Iterator

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

        # Add legacy related edges (backwards compatibility)
        for related_id in lesson.related_ids:
            # Only add if not already covered by typed relationships
            if not any(r.target == related_id for r in lesson.relationships):
                self.graph.add_edge(lesson.id, related_id, relation="related")

    def remove_lesson(self, lesson_id: str) -> None:
        """Remove a lesson from the graph."""
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
        return list(related)

    def get_relationships(self, lesson_id: str) -> list[dict]:
        """Get all relationships with full metadata for a lesson.

        Returns list of dicts with: target, type, weight, context, bidirectional
        """
        relationships = []
        for _, target, data in self.graph.out_edges(lesson_id, data=True):
            if data.get("relation") != "parent":
                relationships.append({
                    "target": target,
                    "type": data.get("relation", "related"),
                    "weight": data.get("weight", 0.5),
                    "context": data.get("context", []),
                    "bidirectional": data.get("bidirectional", True),
                })
        return relationships

    def get_by_relationship_type(self, lesson_id: str, rel_type: str) -> list[str]:
        """Get lessons connected by a specific relationship type."""
        return self.get_related(lesson_id, relation_type=rel_type)

    def get_prerequisites(self, lesson_id: str) -> list[str]:
        """Get prerequisite lessons (must know/do first)."""
        return self.get_by_relationship_type(lesson_id, "prerequisite")

    def get_next_in_sequence(self, lesson_id: str) -> list[str]:
        """Get lessons that should come next in sequence."""
        return self.get_by_relationship_type(lesson_id, "sequence_next")

    def get_alternatives(self, lesson_id: str) -> list[str]:
        """Get alternative approaches to the same problem."""
        return self.get_by_relationship_type(lesson_id, "alternative")

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

        visited = set()
        paths = []

        def traverse(node_id: str, current_path: list[str], current_depth: int):
            if current_depth > depth:
                return
            if node_id in visited:
                return

            visited.add(node_id)
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
        return list(visited), paths

    def get_ancestors(self, lesson_id: str) -> list[str]:
        """Get all ancestors (parents, grandparents, etc.) up to root."""
        ancestors = []
        current = lesson_id
        while True:
            parent = self.get_parent(current)
            if parent is None:
                break
            ancestors.append(parent)
            current = parent
        return ancestors

    def get_descendants(self, lesson_id: str) -> list[str]:
        """Get all descendants (children, grandchildren, etc.)."""
        descendants = []

        def collect(node_id: str):
            for child_id in self.get_children(node_id):
                descendants.append(child_id)
                collect(child_id)

        collect(lesson_id)
        return descendants

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

    def find_path(self, from_id: str, to_id: str) -> list[str] | None:
        """Find shortest path between two lessons."""
        try:
            # Use undirected view for path finding
            undirected = self.graph.to_undirected()
            path = nx.shortest_path(undirected, from_id, to_id)
            return path
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

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

    def load_from_lessons(self, lessons: list[Lesson]) -> None:
        """Load graph from a list of lessons."""
        self.graph.clear()
        for lesson in lessons:
            self.add_lesson(lesson)