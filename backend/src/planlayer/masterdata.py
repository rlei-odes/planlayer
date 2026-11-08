"""Master data in Planlayer's tables (ADR 0012): the member tree, manual members, local
overrides on synced members.

Manual members are edited directly. A synced member can be renamed or moved; the override is
kept in ``member_override`` / ``edge_override`` and survives syncs, while ``member.name`` and
``hier_edge.parent_id`` hold the effective values every reader uses.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import Connection, Engine, delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from planlayer.access import AccessError, User
from planlayer.store.schema import (
    COST_CENTER,
    NO_ENTITY,
    closure,
    edge_override,
    hier_edge,
    member,
    member_override,
)
from planlayer.store.uow import unit_of_work
from planlayer.sync import DIMENSIONS, rebuild_closure


class MasterDataError(ValueError):
    """A change to master data that can't be made as asked; nothing was changed."""


def require_editor(user: User) -> None:
    if not (user.is_controller or user.is_admin):
        raise AccessError("only a controller or admin may edit master data")


def _dimension(dataset: str) -> tuple[int, int | None]:
    if dataset not in DIMENSIONS:
        raise MasterDataError(f"unknown dataset {dataset}")
    return DIMENSIONS[dataset]


@dataclass
class MemberRow:
    code: str
    name: str
    source_name: str | None
    origin: str  # source / manual
    is_group: bool
    depth: int
    parent: str | None  # effective
    source_parent: str | None
    renamed: bool
    moved: bool
    entity_code: str | None
    owner: str | None
    account_type: str | None
    valid_from: date | None
    valid_to: date | None


def tree(engine: Engine, user: User, dataset: str) -> list[MemberRow]:
    """The dataset's members, depth first in hierarchy order; members outside the hierarchy
    (no longer delivered, or entities) follow at depth 0."""
    require_editor(user)
    dim, hier_id = _dimension(dataset)
    with engine.connect() as conn:
        members = {
            r.member_id: r for r in conn.execute(select(member).where(member.c.dim_id == dim))
        }
        renamed = set(conn.execute(select(member_override.c.member_id)).scalars())
        moved = set()
        edges = {}
        if hier_id is not None:
            moved = set(
                conn.execute(
                    select(edge_override.c.member_id).where(edge_override.c.hier_id == hier_id)
                ).scalars()
            )
            edges = {
                e.member_id: e
                for e in conn.execute(
                    select(hier_edge)
                    .where(hier_edge.c.hier_id == hier_id)
                    .order_by(hier_edge.c.sort_order)
                )
            }
    code = {m: r.code for m, r in members.items()}
    children: dict[int | None, list[int]] = defaultdict(list)
    for m, e in edges.items():
        children[e.parent_id].append(m)
    out: list[MemberRow] = []

    def row(m: int, depth: int) -> MemberRow:
        r, e = members[m], edges.get(m)
        return MemberRow(
            r.code,
            r.name,
            r.source_name,
            r.origin,
            r.is_group,
            depth,
            code.get(e.parent_id) if e and e.parent_id else None,
            code.get(e.source_parent_id) if e and e.source_parent_id else None,
            m in renamed,
            m in moved,
            r.entity_code,
            r.owner,
            r.account_type,
            r.valid_from,
            r.valid_to,
        )

    def visit(m: int, depth: int) -> None:
        out.append(row(m, depth))
        for child in children[m]:
            visit(child, depth + 1)

    for root in children[None]:
        visit(root, 0)
    placed = set(edges)
    out += [row(m, 0) for m in sorted(members, key=lambda m: code[m]) if m not in placed]
    return out


def _find(conn: Connection, dim: int, code: str) -> int:
    found = conn.execute(
        select(member.c.member_id).where(member.c.dim_id == dim, member.c.code == code)
    ).scalar()
    if found is None:
        raise MasterDataError(f"{code} not found")
    return int(found)


def _parent(
    conn: Connection, dim: int, hier_id: int, member_id: int | None, code: str | None
) -> int | None:
    """A valid new parent for ``member_id``: a group of the same dimension, not below it."""
    if code is None:
        return None
    parent_id = _find(conn, dim, code)
    is_group = conn.execute(
        select(member.c.is_group).where(member.c.member_id == parent_id)
    ).scalar()
    if not is_group:
        raise MasterDataError(f"{code} is not a group")
    if member_id is not None:
        below = conn.execute(
            select(closure.c.descendant_id).where(
                closure.c.hier_id == hier_id,
                closure.c.ancestor_id == member_id,
                closure.c.descendant_id == parent_id,
            )
        ).first()
        if below is not None:
            raise MasterDataError(f"{code} is below the member itself")
    return parent_id


def add_member(
    engine: Engine,
    user: User,
    dataset: str,
    code: str,
    name: str,
    *,
    parent: str | None = None,
    is_group: bool = False,
    entity_code: str | None = None,
    owner: str | None = None,
    account_type: str | None = None,
) -> None:
    """A manual member; the same page builds master data from scratch."""
    require_editor(user)
    dim, hier_id = _dimension(dataset)
    code, name = code.strip(), name.strip()
    if not code or not name:
        raise MasterDataError("a member needs a code and a name")
    with unit_of_work(engine, user) as conn:
        exists = conn.execute(
            select(member.c.member_id).where(member.c.dim_id == dim, member.c.code == code)
        ).first()
        if exists is not None:
            raise MasterDataError(f"{code} exists")
        parent_id = _parent(conn, dim, hier_id, None, parent) if hier_id is not None else None
        member_id = conn.execute(
            insert(member)
            .values(
                dim_id=dim,
                code=code,
                name=name,
                is_group=is_group,
                origin="manual",
                entity_code=(entity_code or NO_ENTITY)
                if dim == COST_CENTER and not is_group
                else None,
                owner=owner,
                account_type=account_type,
            )
            .returning(member.c.member_id)
        ).scalar_one()
        if hier_id is not None:
            conn.execute(
                insert(hier_edge).values(
                    hier_id=hier_id, member_id=member_id, parent_id=parent_id, origin="manual"
                )
            )
            rebuild_closure(conn)


def rename(engine: Engine, user: User, dataset: str, code: str, name: str) -> None:
    """Rename a manual member, or override a synced member's name."""
    require_editor(user)
    dim, _ = _dimension(dataset)
    if not name.strip():
        raise MasterDataError("a member needs a name")
    with unit_of_work(engine, user) as conn:
        member_id = _find(conn, dim, code)
        origin = conn.execute(
            select(member.c.origin).where(member.c.member_id == member_id)
        ).scalar()
        if origin == "source":
            stmt = pg_insert(member_override).values(
                member_id=member_id, name=name, set_by=user.login
            )
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=[member_override.c.member_id],
                    set_={"name": name, "set_by": user.login},
                )
            )
        conn.execute(update(member).where(member.c.member_id == member_id).values(name=name))


def move(engine: Engine, user: User, dataset: str, code: str, parent: str | None) -> None:
    """Move a manual member, or override a synced member's parent; ``None``: a root."""
    require_editor(user)
    dim, hier_id = _dimension(dataset)
    if hier_id is None:
        raise MasterDataError(f"{dataset} have no hierarchy")
    with unit_of_work(engine, user) as conn:
        member_id = _find(conn, dim, code)
        parent_id = _parent(conn, dim, hier_id, member_id, parent)
        edge = conn.execute(
            select(hier_edge.c.origin).where(
                hier_edge.c.hier_id == hier_id, hier_edge.c.member_id == member_id
            )
        ).first()
        if edge is None:
            raise MasterDataError(f"{code} is not in the hierarchy")
        if edge.origin == "source":
            stmt = pg_insert(edge_override).values(
                hier_id=hier_id, member_id=member_id, parent_id=parent_id, set_by=user.login
            )
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=[edge_override.c.hier_id, edge_override.c.member_id],
                    set_={"parent_id": parent_id, "set_by": user.login},
                )
            )
        conn.execute(
            update(hier_edge)
            .where(hier_edge.c.hier_id == hier_id, hier_edge.c.member_id == member_id)
            .values(parent_id=parent_id)
        )
        rebuild_closure(conn)


def reset(engine: Engine, user: User, dataset: str, code: str) -> None:
    """Remove a synced member's overrides: back to the source's name and parent."""
    require_editor(user)
    dim, hier_id = _dimension(dataset)
    with unit_of_work(engine, user) as conn:
        member_id = _find(conn, dim, code)
        conn.execute(delete(member_override).where(member_override.c.member_id == member_id))
        conn.execute(
            update(member)
            .where(member.c.member_id == member_id, member.c.origin == "source")
            .values(name=member.c.source_name)
        )
        if hier_id is not None:
            conn.execute(
                delete(edge_override).where(
                    edge_override.c.hier_id == hier_id, edge_override.c.member_id == member_id
                )
            )
            conn.execute(
                update(hier_edge)
                .where(
                    hier_edge.c.hier_id == hier_id,
                    hier_edge.c.member_id == member_id,
                    hier_edge.c.origin == "source",
                )
                .values(parent_id=hier_edge.c.source_parent_id)
            )
            rebuild_closure(conn)


def set_attributes(
    engine: Engine,
    user: User,
    dataset: str,
    code: str,
    *,
    entity_code: str | None = None,
    owner: str | None = None,
    account_type: str | None = None,
) -> None:
    """Attributes of a manual member; a synced member's come from the source (its planner is
    set on the responsibilities page)."""
    require_editor(user)
    dim, _ = _dimension(dataset)
    with unit_of_work(engine, user) as conn:
        member_id = _find(conn, dim, code)
        r = conn.execute(select(member).where(member.c.member_id == member_id)).one()
        if r.origin != "manual":
            raise MasterDataError(f"{code} comes from the source; change it there")
        conn.execute(
            update(member)
            .where(member.c.member_id == member_id)
            .values(
                entity_code=(entity_code or NO_ENTITY)
                if dim == COST_CENTER and not r.is_group
                else None,
                owner=owner,
                account_type=account_type,
            )
        )


def delete_member(engine: Engine, user: User, dataset: str, code: str) -> None:
    """Delete a manual member nothing refers to."""
    require_editor(user)
    dim, hier_id = _dimension(dataset)
    try:
        with unit_of_work(engine, user) as conn:
            member_id = _find(conn, dim, code)
            origin = conn.execute(
                select(member.c.origin).where(member.c.member_id == member_id)
            ).scalar()
            if origin != "manual":
                raise MasterDataError(f"{code} comes from the source and can't be deleted")
            has_children = conn.execute(
                select(hier_edge.c.member_id).where(hier_edge.c.parent_id == member_id).limit(1)
            ).first()
            if has_children is not None:
                raise MasterDataError(f"{code} has members below it")
            conn.execute(delete(hier_edge).where(hier_edge.c.member_id == member_id))
            conn.execute(delete(member).where(member.c.member_id == member_id))
            if hier_id is not None:
                rebuild_closure(conn)
    except IntegrityError:
        raise MasterDataError(f"{code} is still used (plan values, rounds or settings)") from None
