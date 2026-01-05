from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class Rubric:
    good: float = 1.0
    partial: float = 0.5
    bad: float = 0.0

    def to_dict(self) -> Dict[str, float]:
        return {"good": float(self.good), "partial": float(self.partial), "bad": float(self.bad)}

    @classmethod
    def from_dict(cls, d: Dict[str, Any] | None) -> "Rubric":
        d = d or {}
        return cls(
            good=float(d.get("good", 1.0)),
            partial=float(d.get("partial", 0.5)),
            bad=float(d.get("bad", 0.0)),
        )


@dataclass
class Node:
    code: str
    label: str = ""
    rubric: Optional[Rubric] = None
    children: List["Node"] = field(default_factory=list)

    def level(self) -> int:
        # "1" => 0, "1.2" => 1, "1.2.1" => 2
        return max(0, len(self.code.split(".")) - 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "rubric": self.rubric.to_dict() if self.rubric else None,
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Node":
        return cls(
            code=str(d.get("code", "")).strip(),
            label=str(d.get("label", "") or ""),
            rubric=Rubric.from_dict(d.get("rubric")) if d.get("rubric") else None,
            children=[cls.from_dict(c) for c in (d.get("children") or [])],
        )


@dataclass
class Scheme:
    exercises: List[Node] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"version": 1, "exercises": [e.to_dict() for e in self.exercises]}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Scheme":
        ex = [Node.from_dict(e) for e in (d.get("exercises") or [])]
        return cls(exercises=ex)


# ----------------- Basic helpers -----------------

def make_default_scheme(n_exercises: int = 1) -> Scheme:
    """Crée un barème par défaut : Ex 1..N, avec 2 sous-niveaux (1.1, 1.2) sans sous-sousniveau."""
    sch = Scheme()
    for i in range(1, max(1, int(n_exercises)) + 1):
        ex = Node(code=str(i), label=f"Exercice {i}", rubric=None, children=[])
        ex.children.append(Node(code=f"{i}.1", label=f"Ex {i}.1", rubric=Rubric(), children=[]))
        ex.children.append(Node(code=f"{i}.2", label=f"Ex {i}.2", rubric=Rubric(), children=[]))
        sch.exercises.append(ex)
    return sch


def ensure_scheme_dict(value: Any) -> Dict[str, Any]:
    """Assure un dict valide pour stocker dans settings['grading_scheme']."""
    if isinstance(value, dict) and "exercises" in value:
        return value
    if isinstance(value, Scheme):
        return value.to_dict()
    # défaut
    return make_default_scheme(1).to_dict()


def scheme_from_dict(d: Dict[str, Any]) -> Scheme:
    return Scheme.from_dict(ensure_scheme_dict(d))


def scheme_to_dict(s: Scheme) -> Dict[str, Any]:
    return s.to_dict()


def find_node(scheme: Scheme, code: str) -> Optional[Tuple[Node, Optional[Node]]]:
    """Retourne (node, parent) ou None."""
    def rec(nodes: List[Node], parent: Optional[Node]) -> Optional[Tuple[Node, Optional[Node]]]:
        for n in nodes:
            if n.code == code:
                return (n, parent)
            r = rec(n.children, n)
            if r:
                return r
        return None
    return rec(scheme.exercises, None)


def leaf_nodes(scheme: Scheme) -> List[Node]:
    """Feuilles notables : n.X ou n.X.Y sans enfants, niveaux 1 ou 2."""
    out: List[Node] = []

    def rec(n: Node):
        if n.children:
            for c in n.children:
                rec(c)
            return
        if n.level() in (1, 2):
            out.append(n)

    for ex in scheme.exercises:
        rec(ex)
    return out


def points_for(scheme: Scheme, leaf_code: str, result: str) -> float:
    found = find_node(scheme, leaf_code)
    if not found:
        return 0.0
    node, _parent = found
    rub = node.rubric or Rubric()
    if result == "good":
        return float(rub.good)
    if result == "partial":
        return float(rub.partial)
    return float(rub.bad)


# ----------------- Editing operations -----------------

def regenerate_exercises(scheme: Scheme, n: int) -> None:
    scheme.exercises = make_default_scheme(n).exercises


def _next_child_index(parent: Node) -> int:
    # For exercise "2", children are "2.1", "2.2"... so next index is max+1
    indices: List[int] = []
    prefix = parent.code + "."
    for c in parent.children:
        if c.code.startswith(prefix):
            parts = c.code.split(".")
            if len(parts) >= 2:
                try:
                    indices.append(int(parts[1]))
                except Exception:
                    pass
    return (max(indices) + 1) if indices else 1


def add_exercise(scheme: Scheme) -> str:
    indices = []
    for ex in scheme.exercises:
        try:
            indices.append(int(ex.code))
        except Exception:
            pass
    new_i = (max(indices) + 1) if indices else 1
    ex = Node(code=str(new_i), label=f"Exercice {new_i}", rubric=None, children=[])
    ex.children.append(Node(code=f"{new_i}.1", label=f"Ex {new_i}.1", rubric=Rubric(), children=[]))
    ex.children.append(Node(code=f"{new_i}.2", label=f"Ex {new_i}.2", rubric=Rubric(), children=[]))
    scheme.exercises.append(ex)
    return ex.code


def add_sublevel(scheme: Scheme, exercise_code: str) -> str:
    found = find_node(scheme, exercise_code)
    if not found:
        raise ValueError("Exercice introuvable.")
    ex, _ = found
    if ex.level() != 0:
        raise ValueError("Sélectionne un exercice (niveau 0).")
    idx = _next_child_index(ex)
    code = f"{ex.code}.{idx}"
    ex.children.append(Node(code=code, label=f"Ex {code}", rubric=Rubric(), children=[]))
    return code


def add_subsublevel(scheme: Scheme, sublevel_code: str) -> str:
    found = find_node(scheme, sublevel_code)
    if not found:
        raise ValueError("Niveau introuvable.")
    node, _ = found
    if node.level() != 1:
        raise ValueError("Sélectionne un sous-niveau (niveau 1).")
    # When a node gains children, its rubric becomes None (sum of children)
    node.rubric = None

    # next index among .Y
    indices: List[int] = []
    prefix = node.code + "."
    for c in node.children:
        if c.code.startswith(prefix):
            parts = c.code.split(".")
            if len(parts) >= 3:
                try:
                    indices.append(int(parts[2]))
                except Exception:
                    pass
    new_i = (max(indices) + 1) if indices else 1
    code = f"{node.code}.{new_i}"
    node.children.append(Node(code=code, label=f"Ex {code}", rubric=Rubric(), children=[]))
    return code


def set_label(scheme: Scheme, code: str, label: str) -> None:
    found = find_node(scheme, code)
    if not found:
        return
    node, _ = found
    node.label = label or ""


def set_rubric(scheme: Scheme, code: str, good: float, partial: float, bad: float) -> None:
    found = find_node(scheme, code)
    if not found:
        raise ValueError("Niveau introuvable.")
    node, _ = found
    if node.children:
        raise ValueError("Pas de barème sur un niveau qui a des sous-niveaux (somme des enfants).")
    if node.level() == 0:
        raise ValueError("Pas de barème au niveau exercice.")
    node.rubric = Rubric(good=float(good), partial=float(partial), bad=float(bad))


def delete_node(scheme: Scheme, code: str) -> None:
    found = find_node(scheme, code)
    if not found:
        return
    node, parent = found
    if parent is None:
        # delete exercise
        scheme.exercises = [e for e in scheme.exercises if e.code != code]
    else:
        parent.children = [c for c in parent.children if c.code != code]


def delete_exercise(scheme: Scheme, ex_code: str) -> None:
    scheme.exercises = [e for e in scheme.exercises if e.code != ex_code]
