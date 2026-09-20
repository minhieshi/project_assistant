from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import SourceRoot


STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "into", "your", "are", "not", "use", "using",
    "src", "source", "main", "test", "tests", "readme", "docs", "file", "files", "project", "repo", "repository",
}


@dataclass(frozen=True)
class RepoProfile:
    name: str
    root: str
    file_count: int
    branch: str | None
    commit: str | None
    top_paths: tuple[str, ...]
    languages: tuple[str, ...]
    keywords: tuple[str, ...]
    readme_excerpt: str


@dataclass(frozen=True)
class RepoRoute:
    name: str
    score: float
    reasons: tuple[str, ...]


class RepositoryCatalog:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def rebuild(self, sources: list[SourceRoot], manifest: dict) -> list[RepoProfile]:
        profiles: list[RepoProfile] = []
        for source in sources:
            root = Path(source.path)
            records = [r for r in manifest.values() if r.get("source") == source.name]
            rels = [str(r.get("relative_path", "")) for r in records if r.get("relative_path")]
            top_paths = Counter(p.split("/", 1)[0] for p in rels if p).most_common(12)
            extensions = Counter(Path(p).suffix.lower().lstrip(".") or "text" for p in rels).most_common(10)
            readme = self._read_readme(root)
            keywords = Counter()
            for rel in rels:
                keywords.update(self._tokens(rel.replace("/", " ")))
            keywords.update(self._tokens(readme[:8000]))
            for common in STOPWORDS:
                keywords.pop(common, None)
            record = records[0] if records else {}
            profiles.append(
                RepoProfile(
                    name=source.name,
                    root=str(root),
                    file_count=len(records),
                    branch=record.get("git_branch"),
                    commit=record.get("git_commit"),
                    top_paths=tuple(name for name, _ in top_paths),
                    languages=tuple(name for name, _ in extensions),
                    keywords=tuple(word for word, _ in keywords.most_common(50)),
                    readme_excerpt=readme[:2500].strip(),
                )
            )
        self.path.write_text(json.dumps([asdict(p) for p in profiles], indent=2) + "\n", encoding="utf-8")
        md = ["# Repository catalogue", ""]
        for profile in profiles:
            md.extend([
                f"## {profile.name}",
                "",
                f"- Root: `{profile.root}`",
                f"- Files indexed: {profile.file_count}",
                f"- Git: `{profile.branch or '-'} @ {(profile.commit or '-')[:12]}`",
                f"- Top paths: {', '.join(profile.top_paths) or '-'}",
                f"- Languages/types: {', '.join(profile.languages) or '-'}",
                f"- Keywords: {', '.join(profile.keywords[:20]) or '-'}",
                "",
            ])
            if profile.readme_excerpt:
                md.extend(["### README excerpt", "", profile.readme_excerpt, ""])
        self.path.with_suffix(".md").write_text("\n".join(md).rstrip() + "\n", encoding="utf-8")
        return profiles

    def load(self) -> list[RepoProfile]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return [RepoProfile(**{**item, "top_paths": tuple(item["top_paths"]), "languages": tuple(item["languages"]), "keywords": tuple(item["keywords"])}) for item in raw]

    def route(self, query: str, lexical_hits=(), vector_hits=(), graph_hits=(), limit: int = 3) -> list[RepoRoute]:
        profiles = self.load()
        if not profiles:
            return []
        query_tokens = set(self._tokens(query))
        scores: dict[str, float] = {p.name: 0.0 for p in profiles}
        reasons: dict[str, list[str]] = {p.name: [] for p in profiles}
        roots = {p.name: Path(p.root).resolve() for p in profiles}

        for profile in profiles:
            haystack = set(profile.keywords) | set(self._tokens(" ".join(profile.top_paths))) | set(self._tokens(profile.name))
            overlap = query_tokens & haystack
            if overlap:
                scores[profile.name] += min(6.0, float(len(overlap)))
                reasons[profile.name].append("catalogue:" + ",".join(sorted(overlap)[:5]))
            if profile.name.lower() in query.lower():
                scores[profile.name] += 6.0
                reasons[profile.name].append("repo-name")

        for label, hits, weight in (("lexical", lexical_hits, 5.0), ("vector", vector_hits, 3.0)):
            for rank, hit in enumerate(hits, start=1):
                repo = hit.metadata.get("repo")
                if repo in scores:
                    scores[repo] += weight / (rank + 1)
                    if rank <= 3:
                        reasons[repo].append(f"{label}-hit")

        for hit in graph_hits:
            source = Path(hit.source_path).resolve()
            for name, root in roots.items():
                try:
                    source.relative_to(root)
                except ValueError:
                    continue
                scores[name] += 2.5
                reasons[name].append("graph-hit")
                break

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        positive = [(name, score) for name, score in ranked if score > 0]
        chosen = positive[:limit] if positive else ranked[:limit]
        return [RepoRoute(name, round(score, 3), tuple(dict.fromkeys(reasons[name]))) for name, score in chosen]

    @staticmethod
    def _read_readme(root: Path) -> str:
        for name in ("README.md", "README.markdown", "README.txt", "readme.md"):
            path = root / name
            if path.exists() and path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        return ""

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text) if t.lower() not in STOPWORDS]
