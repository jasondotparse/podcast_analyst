"""The graph as the UI draws it: relationships from Ladybug, evidence from Postgres."""

from uuid import UUID

from .clients.graph_client import GraphClient
from .clients.postgres_client import PostgresClient


MAX_NODES = 300
MAX_THESIS_CLAIMS = 150
MAX_NEIGHBORS = 60
VIEW_TYPES = {"Thesis", "Claim", "Entity", "Episode"}


class GraphView:
    def __init__(self, db: PostgresClient, graph: GraphClient):
        self.db, self.graph = db, graph

    async def theses(self, thesis_id: UUID | None = None) -> dict | None:
        ids = self.graph.thesis_neighborhood(str(thesis_id) if thesis_id else None, MAX_THESIS_CLAIMS + 1)
        if thesis_id and not ids:
            return None
        # Overfetch one claim to report the cap truthfully; keep only its selected episodes.
        rows = self.graph.view_nodes(ids)
        claims = {row[0] for row in rows if row[1] == "Claim"}
        truncated = len(claims) > MAX_THESIS_CLAIMS
        if truncated:
            ids = self.graph.thesis_neighborhood(str(thesis_id) if thesis_id else None, MAX_THESIS_CLAIMS)
        view = await self.subgraph(ids)
        view["truncated"] |= truncated
        # A stale graph thesis is unknown too, rather than a UUID-only node.
        if thesis_id and not any(node["id"] == str(thesis_id) for node in view["nodes"]):
            return None
        return view

    async def neighbors(self, node_id: UUID) -> dict | None:
        ids = self.graph.neighbor_ids(str(node_id), MAX_NEIGHBORS + 1)
        if ids is None:
            return None
        view = await self.subgraph([str(node_id), *ids[:MAX_NEIGHBORS]])
        view["truncated"] |= len(ids) > MAX_NEIGHBORS
        return view if any(node["id"] == str(node_id) for node in view["nodes"]) else None

    async def subgraph(self, ids: list[str]) -> dict:
        ids = list(dict.fromkeys(ids))
        rows = {row[0]: row for row in self.graph.view_nodes(ids[:MAX_NODES]) if row[1] in VIEW_TYPES}
        labels = await self.db.graph_labels([node_id for node_id, row in rows.items() if row[1] != "Entity"])
        nodes = []
        for node_id in ids[:MAX_NODES]:
            if node_id not in rows:
                continue
            _, kind, name, entity_kind = rows[node_id]
            if kind == "Entity":
                label, signal, detail = name, None, {"kind": entity_kind}
            elif node_id in labels:
                label, signal, detail = labels[node_id]
            else:
                continue
            node = {"id": node_id, "type": kind, "label": label, "detail": detail}
            if kind == "Claim":
                node["signal"] = signal
            nodes.append(node)
        links = []
        for source, target, kind, strength in self.graph.view_edges([node["id"] for node in nodes]):
            link = {"source": source, "target": target, "type": kind}
            if strength is not None:
                link["strength"] = strength
            links.append(link)
        return {"nodes": nodes, "links": links, "truncated": len(ids) > MAX_NODES}
