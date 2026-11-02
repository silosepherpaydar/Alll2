"""بازنشرهای شبیه به هم یک تأیید مستقل نیستند."""

from __future__ import annotations

from bazaar_intel.textnorm import jaccard, tokens

COPY_THRESHOLD = 0.72


def copy_clusters(rows: list[dict]) -> list[list[dict]]:
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    bags = [tokens(f"{row.get('title', '')} {row.get('body', '')}") for row in rows]
    for i in range(len(rows)):
        if len(bags[i]) < 4:
            continue
        for j in range(i + 1, len(rows)):
            if len(bags[j]) < 4:
                continue
            if jaccard(bags[i], bags[j]) >= COPY_THRESHOLD:
                union(i, j)
    groups: dict[int, list[dict]] = {}
    for index, row in enumerate(rows):
        groups.setdefault(find(index), []).append(row)
    return list(groups.values())


def earliest(cluster: list[dict]) -> dict:
    def key(row: dict):
        return (row.get("published_at") or "9999", row.get("id") or 0)

    return sorted(cluster, key=key)[0]
