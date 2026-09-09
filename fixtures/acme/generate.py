"""Generate the fictional ACME company: users, groups, sources, documents, OpenFGA tuples,
the golden question set and a Keycloak realm. Deterministic (seeded); never real data.

    uv run python fixtures/acme/generate.py

Outputs go to fixtures/acme/generated/ and are versioned.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

SEED = 42
OUT = Path(__file__).parent / "generated"

N_USERS = 50
N_DOCUMENTS = 500
N_GOLDEN = 300
PASSWORD = "password"

DEPARTMENTS = [
    "direction",
    "rh",
    "finance",
    "magasin-lille",
    "magasin-lyon",
    "it",
    "juridique",
    "stagiaires",
]
# Members of the left group are also members of the right group (OpenFGA group#member nesting).
GROUP_HIERARCHY = [
    ("direction", "rh"),
    ("direction", "finance"),
    ("direction", "juridique"),
]
TOOLS = {
    "tool:mail.send": DEPARTMENTS,
    "tool:hr.export": ["rh"],
    "tool:finance.report": ["finance"],
    "tool:it.reset-password": ["it"],
    "tool:legal.sign": ["juridique"],
}
# Who may resolve a REQUIRE_APPROVAL request for each tool (ADR 0017): management, always.
TOOL_APPROVERS = {tool: ["direction"] for tool in TOOLS}
AGENT = "agent:assistant"
AGENT_CLIENTS = ["assistant", "mailer", "sub"]  # Keycloak client ids → agent:<clientId>

FIRST_NAMES = [
    "Alice",
    "Bruno",
    "Camille",
    "David",
    "Emma",
    "Farid",
    "Gaëlle",
    "Hugo",
    "Inès",
    "Julien",
    "Karim",
    "Léa",
    "Mathis",
    "Nadia",
    "Oscar",
    "Pauline",
    "Quentin",
    "Rania",
    "Samir",
    "Théo",
    "Ursule",
    "Victor",
    "Wassim",
    "Xavier",
    "Yasmine",
    "Zoé",
    "Adrien",
    "Bénédicte",
    "Clément",
    "Dorian",
    "Élise",
    "Fabien",
    "Garance",
    "Hélène",
    "Ismaël",
    "Jade",
    "Kevin",
    "Louise",
    "Marc",
    "Noémie",
    "Olivier",
    "Perrine",
    "Rachid",
    "Sophie",
    "Tristan",
    "Valérie",
    "William",
    "Yann",
    "Zacharie",
    "Anaïs",
]
LAST_NAMES = [
    "Martin",
    "Bernard",
    "Dubois",
    "Thomas",
    "Robert",
    "Richard",
    "Petit",
    "Durand",
    "Leroy",
    "Moreau",
    "Simon",
    "Laurent",
    "Lefebvre",
    "Michel",
    "Garcia",
    "David",
    "Bertrand",
    "Roux",
    "Vincent",
    "Fournier",
    "Morel",
    "Girard",
    "André",
    "Lefèvre",
    "Mercier",
    "Dupont",
    "Lambert",
    "Bonnet",
    "François",
    "Martinez",
    "Legrand",
    "Garnier",
    "Faure",
    "Rousseau",
    "Blanc",
    "Guérin",
    "Muller",
    "Henry",
    "Roussel",
    "Nicolas",
    "Perrin",
    "Morin",
    "Mathieu",
    "Clément",
    "Gauthier",
    "Dumont",
    "Lopez",
    "Fontaine",
    "Chevalier",
    "Robin",
]

TOPICS: dict[str, list[str]] = {
    "direction": ["plan stratégique", "comité de direction", "budget groupe", "acquisition"],
    "rh": ["grille de salaires", "entretiens annuels", "recrutement", "procédure disciplinaire"],
    "finance": ["clôture trimestrielle", "trésorerie", "audit interne", "notes de frais"],
    "magasin-lille": ["planning Lille", "inventaire Lille", "objectifs ventes Lille"],
    "magasin-lyon": ["planning Lyon", "inventaire Lyon", "objectifs ventes Lyon"],
    "it": ["incident production", "architecture SI", "gestion des accès", "sauvegardes"],
    "juridique": ["contrat fournisseur", "contentieux", "RGPD", "baux commerciaux"],
    "stagiaires": ["guide d'accueil", "charte informatique", "plan de formation"],
}
CONFIDENTIALITY = ["public", "internal", "confidential", "restricted"]
CONFIDENTIALITY_WEIGHTS = [0.15, 0.50, 0.25, 0.10]

SENTENCES = [
    "Ce document présente {topic} pour la période {period}.",
    "Les chiffres de {topic} sont consolidés par le service {dept}.",
    "Toute diffusion de {topic} en dehors du service {dept} est interdite.",
    "Le point d'étape sur {topic} aura lieu le {day} du mois prochain.",
    "Les actions retenues concernant {topic} sont listées en annexe.",
    "Le responsable de {topic} est joignable par le canal habituel.",
    "Version {version} validée par la direction du service {dept}.",
]
PERIODS = ["T1 2026", "T2 2026", "T3 2026", "S1 2026", "exercice 2025", "année 2026"]

QUESTION_TEMPLATES = [
    "Que dit le dernier document sur {topic} ?",
    "Résume les informations disponibles sur {topic}.",
    "Quels documents parlent de {topic} ?",
    "Y a-t-il une mise à jour concernant {topic} ?",
    "Donne-moi les points clés sur {topic}.",
]


@dataclass
class User:
    id: str
    username: str
    first_name: str
    last_name: str
    email: str
    department: str
    groups: list[str]
    intern: bool = False


@dataclass
class Group:
    id: str
    name: str
    parents: list[str] = field(default_factory=list)


@dataclass
class Source:
    id: str
    name: str
    path: str
    viewers: list[str]
    editors: list[str]


@dataclass
class Document:
    id: str
    title: str
    source: str
    path: str
    department: str
    topic: str
    confidentiality: str
    viewers: list[str]
    editors: list[str]
    body: str


@dataclass
class GoldenQuestion:
    id: str
    subject: str
    actor: str
    question: str
    topic: str
    expected_visible: list[str]
    expected_invisible: list[str]


def slug(text: str) -> str:
    table = str.maketrans("àâäéèêëîïôöùûüç'", "aaaeeeeiioouuuc-")
    return text.lower().translate(table).replace(" ", "-")


def make_groups() -> list[Group]:
    groups = {d: Group(id=f"group:{d}", name=d) for d in DEPARTMENTS}
    for child, parent in GROUP_HIERARCHY:
        groups[child].parents.append(f"group:{parent}")
    return list(groups.values())


def make_users(rng: random.Random) -> list[User]:
    users: list[User] = []
    departments = [d for d in DEPARTMENTS if d != "stagiaires"]
    # 5 in direction, 6 interns spread over departments, the rest evenly distributed.
    assignments = ["direction"] * 5
    non_direction = [d for d in departments if d != "direction"]
    while len(assignments) < N_USERS:
        assignments.append(non_direction[len(assignments) % len(non_direction)])
    interns = set(rng.sample(range(5, N_USERS), 6))
    seen: set[str] = set()
    for i in range(N_USERS):
        first, last = FIRST_NAMES[i], LAST_NAMES[i]
        username = f"{slug(first)}.{slug(last)}"
        if username in seen:
            username += str(i)
        seen.add(username)
        dept = assignments[i]
        groups = [f"group:{dept}"]
        intern = i in interns
        if intern:
            groups.append("group:stagiaires")
        users.append(
            User(
                id=f"user:{username}",
                username=username,
                first_name=first,
                last_name=last,
                email=f"{username}@acme.example",
                department=dept,
                groups=groups,
                intern=intern,
            )
        )
    return users


def make_sources(users: list[User], rng: random.Random) -> list[Source]:
    sources: list[Source] = []
    for dept in DEPARTMENTS:
        members = [u for u in users if u.department == dept and not u.intern]
        editors = [u.id for u in rng.sample(members, min(2, len(members)))] if members else []
        sources.append(
            Source(
                id=f"source:nextcloud-{dept}",
                name=f"Nextcloud — {dept}",
                path=f"/ACME/{dept}",
                viewers=[f"group:{dept}"],
                editors=editors,
            )
        )
    sources.append(
        Source(
            id="source:nextcloud-public",
            name="Nextcloud — public",
            path="/ACME/public",
            viewers=[f"group:{d}" for d in DEPARTMENTS],
            editors=[u.id for u in users if u.department == "direction"][:2],
        )
    )
    return sources


def make_documents(users: list[User], sources: list[Source], rng: random.Random) -> list[Document]:
    docs: list[Document] = []
    by_dept = {s.id.split("nextcloud-")[1]: s for s in sources}
    for i in range(N_DOCUMENTS):
        dept = rng.choice(DEPARTMENTS)
        topic = rng.choice(TOPICS[dept])
        confidentiality = rng.choices(CONFIDENTIALITY, CONFIDENTIALITY_WEIGHTS)[0]
        source = by_dept["public"] if confidentiality == "public" else by_dept[dept]
        viewers: list[str] = []
        editors: list[str] = []
        staff = [u for u in users if u.department == dept and not u.intern]
        if confidentiality == "confidential":
            # Shared with a few named people, possibly outside the department.
            viewers = [u.id for u in rng.sample(users, rng.randint(1, 3))]
        if confidentiality == "restricted":
            # Not on the department share at all: explicit editors only (typically managers).
            source = Source(
                id=f"source:restricted-{dept}", name="", path="", viewers=[], editors=[]
            )
            editors = [u.id for u in rng.sample(staff, min(len(staff), rng.randint(1, 2)))]
            if dept != "direction":
                editors.append(rng.choice([u.id for u in users if u.department == "direction"]))
        period = rng.choice(PERIODS)
        title = f"{topic.capitalize()} — {period}"
        body = " ".join(
            s.format(
                topic=topic,
                period=period,
                dept=dept,
                day=rng.randint(1, 28),
                version=f"{rng.randint(1, 4)}.{rng.randint(0, 9)}",
            )
            for s in rng.sample(SENTENCES, 4)
        )
        docs.append(
            Document(
                id=f"document:acme-{i:04d}",
                title=title,
                source=source.id,
                path=f"{source.path or '/ACME/restricted/' + dept}/{slug(topic)}-{i:04d}.md",
                department=dept,
                topic=topic,
                confidentiality=confidentiality,
                viewers=viewers,
                editors=editors,
                body=body,
            )
        )
    return docs


def effective_groups(user: User, groups: dict[str, Group]) -> set[str]:
    result: set[str] = set()
    stack = list(user.groups)
    while stack:
        g = stack.pop()
        if g in result:
            continue
        result.add(g)
        stack.extend(groups[g].parents)
    return result


def can_view(
    user: User, doc: Document, sources: dict[str, Source], groups: dict[str, Group]
) -> bool:
    """Oracle mirroring docs/authz-model.fga: direct ACL or inherited from the parent source."""
    principals = {user.id} | effective_groups(user, groups)
    if principals & set(doc.viewers) or principals & set(doc.editors):
        return True
    source = sources.get(doc.source)
    if source is None:
        return False
    return bool(principals & set(source.viewers) or principals & set(source.editors))


def make_tuples(
    users: list[User], groups: list[Group], sources: list[Source], docs: list[Document]
) -> list[dict[str, str]]:
    tuples: list[dict[str, str]] = []

    def add(user: str, relation: str, obj: str) -> None:
        tuples.append({"user": user, "relation": relation, "object": obj})

    for u in users:
        for g in u.groups:
            add(u.id, "member", g)
        add(u.id, "can_act_on_behalf_of", AGENT)
    for g in groups:
        for parent in g.parents:
            add(f"{g.id}#member", "member", parent)
    for s in sources:
        for v in s.viewers:
            add(_principal(v), "viewer", s.id)
        for e in s.editors:
            add(_principal(e), "editor", s.id)
    for d in docs:
        if d.source in {s.id for s in sources}:
            add(d.source, "parent", d.id)
        for v in d.viewers:
            add(_principal(v), "viewer", d.id)
        for e in d.editors:
            add(_principal(e), "editor", d.id)
    for tool, depts in TOOLS.items():
        for dept in depts:
            add(f"group:{dept}#member", "can_invoke", tool)
    for tool, depts in TOOL_APPROVERS.items():
        for dept in depts:
            add(f"group:{dept}#member", "approver", tool)
    return tuples


def make_policy_tuples(users: list[User], groups: list[Group]) -> list[dict[str, str]]:
    """Organization policy that no source connector provides: hierarchy, tools, agent bindings."""
    tuples: list[dict[str, str]] = []
    for g in groups:
        for parent in g.parents:
            tuples.append({"user": f"{g.id}#member", "relation": "member", "object": parent})
    for tool, depts in TOOLS.items():
        for dept in depts:
            tuples.append(
                {"user": f"group:{dept}#member", "relation": "can_invoke", "object": tool}
            )
    for tool, depts in TOOL_APPROVERS.items():
        for dept in depts:
            tuples.append({"user": f"group:{dept}#member", "relation": "approver", "object": tool})
    for u in users:
        tuples.append({"user": u.id, "relation": "can_act_on_behalf_of", "object": AGENT})
    return tuples


def _principal(ref: str) -> str:
    return f"{ref}#member" if ref.startswith("group:") else ref


def make_golden(
    users: list[User],
    groups: list[Group],
    sources: list[Source],
    docs: list[Document],
    rng: random.Random,
) -> list[GoldenQuestion]:
    group_map = {g.id: g for g in groups}
    source_map = {s.id: s for s in sources}
    by_topic: dict[str, list[Document]] = {}
    for d in docs:
        by_topic.setdefault(d.topic, []).append(d)
    golden: list[GoldenQuestion] = []
    attempts = 0
    while len(golden) < N_GOLDEN and attempts < N_GOLDEN * 20:
        attempts += 1
        user = rng.choice(users)
        topic = rng.choice(list(by_topic))
        candidates = by_topic[topic]
        visible = sorted(d.id for d in candidates if can_view(user, d, source_map, group_map))
        invisible = sorted(d.id for d in candidates if d.id not in set(visible))
        # Keep the set interesting: a mix of fully denied, fully allowed and partial cases.
        if not visible and not invisible:
            continue
        golden.append(
            GoldenQuestion(
                id=f"q-{len(golden):03d}",
                subject=user.id,
                actor=AGENT,
                question=rng.choice(QUESTION_TEMPLATES).format(topic=topic),
                topic=topic,
                expected_visible=visible,
                expected_invisible=invisible,
            )
        )
    return golden


def make_keycloak_realm(users: list[User]) -> dict[str, object]:
    return {
        "realm": "acme",
        "enabled": True,
        "sslRequired": "none",
        "accessTokenLifespan": 3600,
        "groups": [{"name": d, "path": f"/{d}"} for d in DEPARTMENTS],
        "users": [
            {
                "username": u.username,
                "enabled": True,
                "email": u.email,
                "emailVerified": True,
                "firstName": u.first_name,
                "lastName": u.last_name,
                "credentials": [{"type": "password", "value": PASSWORD, "temporary": False}],
                "groups": [f"/{g.split(':', 1)[1]}" for g in u.groups],
            }
            for u in users
        ],
        "clients": [
            {
                "clientId": "headofcontext",
                "name": "HeadOfContext",
                "enabled": True,
                "publicClient": False,
                "secret": "hoc-dev-secret",
                "directAccessGrantsEnabled": True,
                "standardFlowEnabled": True,
                "serviceAccountsEnabled": True,
                "attributes": {"standard.token.exchange.enabled": "true"},
                # No explicit client scopes: the realm defaults (basic, profile, email, roles)
                # apply, so access tokens carry sub and preferred_username.
                "protocolMappers": [
                    {
                        "name": "groups",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-group-membership-mapper",
                        "config": {
                            "claim.name": "groups",
                            "full.path": "true",
                            "id.token.claim": "true",
                            "access.token.claim": "true",
                            "userinfo.token.claim": "true",
                        },
                    },
                    {
                        "name": "audience",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "config": {
                            "included.client.audience": "headofcontext",
                            "access.token.claim": "true",
                        },
                    },
                    # Standard token exchange only issues audiences the requester can already
                    # produce: expose the downstream client through an audience mapper.
                    {
                        "name": "nextcloud-audience",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "config": {
                            "included.client.audience": "nextcloud",
                            "access.token.claim": "true",
                        },
                    },
                ],
            },
            {
                "clientId": "nextcloud",
                "name": "Nextcloud",
                "enabled": True,
                "publicClient": False,
                "secret": "nextcloud-dev-secret",
                "standardFlowEnabled": True,
                "redirectUris": ["http://localhost:8090/*"],
            },
            # Agents are confidential clients using client credentials (ADR 0011). The audience
            # mapper puts the service in `aud`; the service maps the token to `agent:<clientId>`.
            *[
                {
                    "clientId": agent,
                    "name": f"ACME agent {agent}",
                    "enabled": True,
                    "publicClient": False,
                    "secret": f"{agent}-dev-secret",
                    "serviceAccountsEnabled": True,
                    "standardFlowEnabled": False,
                    "protocolMappers": [
                        {
                            "name": "audience",
                            "protocol": "openid-connect",
                            "protocolMapper": "oidc-audience-mapper",
                            "config": {
                                "included.client.audience": "headofcontext",
                                "access.token.claim": "true",
                            },
                        }
                    ],
                }
                for agent in AGENT_CLIENTS
            ],
        ],
    }


def dump(name: str, data: object) -> None:
    path = OUT / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main() -> None:
    rng = random.Random(SEED)
    OUT.mkdir(parents=True, exist_ok=True)
    groups = make_groups()
    users = make_users(rng)
    sources = make_sources(users, rng)
    docs = make_documents(users, sources, rng)
    tuples = make_tuples(users, groups, sources, docs)
    golden = make_golden(users, groups, sources, docs, rng)
    dump("users.json", [asdict(u) for u in users])
    dump("groups.json", [asdict(g) for g in groups])
    dump("sources.json", [asdict(s) for s in sources])
    dump("documents.json", [asdict(d) for d in docs])
    dump("tuples.json", tuples)
    dump("policy-tuples.json", make_policy_tuples(users, groups))
    dump("golden.json", [asdict(q) for q in golden])
    dump("keycloak-realm.json", make_keycloak_realm(users))
    stats = {
        "users": len(users),
        "groups": len(groups),
        "sources": len(sources),
        "documents": len(docs),
        "tuples": len(tuples),
        "golden": len(golden),
        "confidentiality": {
            c: sum(1 for d in docs if d.confidentiality == c) for c in CONFIDENTIALITY
        },
        "golden_fully_denied": sum(1 for q in golden if not q.expected_visible),
        "golden_fully_allowed": sum(1 for q in golden if not q.expected_invisible),
    }
    print(json.dumps(stats, indent=1))


if __name__ == "__main__":
    main()
