"""Workflow per round x cost center (ADR 0008): transitions and the status page.

A cost center's approvers are the users assigned to the nearest node above it in the cost
center hierarchy; the controller may approve every cost center (ADR 0009). Who may act:

- **submit:** whoever may write the cost center (owner, deputy, controller);
- **approve, reject:** its approvers and the controller, but not someone who plans it;
- **reopen:** the controller.

The status page shows the enabled cost centers the user may read, with counts rolled up per
node over them, and per cost center the actions the user may take.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, cast

from sqlalchemy import Connection, Engine, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from planlayer.access import AccessError, User, approvers, cost_center_rights, plans
from planlayer.domain.workflow import (
    ACTIONS,
    STATUSES,
    Action,
    Status,
    WorkflowError,
    allows,
    is_overdue,
    refusal,
    transition,
)
from planlayer.rounds import NotFoundError, RoundRef, get_round, valid_cost_centers
from planlayer.store.schema import (
    COST_CENTER,
    H_COST_CENTER,
    MODULE_COST_CENTER,
    closure,
    hier_edge,
    member,
    round_member,
    workflow_event,
    workflow_status,
)
from planlayer.store.uow import unit_of_work


@dataclass
class StatusRow:
    kind: Literal["node", "cost_center"]
    code: str
    name: str
    depth: int
    # Nodes: enabled cost centers below, per status.
    counts: dict[Status, int] = field(default_factory=dict)
    # Cost centers:
    status: Status | None = None
    owner: str | None = None
    approvers: list[str] = field(default_factory=list)  # empty: the controller
    changed_at: datetime | None = None
    changed_by: str | None = None
    overdue: bool = False
    actions: list[Action] = field(default_factory=list)  # what this user may do now


@dataclass
class NotPlanned:
    code: str
    name: str
    reason: Literal["new", "disabled"]


@dataclass
class Overview:
    round: RoundRef
    rows: list[StatusRow]
    not_planned: list[NotPlanned]


def enabled_members(conn: Connection, version_id: int) -> dict[int, bool]:
    return {
        m: e
        for m, e in conn.execute(
            select(round_member.c.member_id, round_member.c.enabled).where(
                round_member.c.version_id == version_id,
                round_member.c.module == MODULE_COST_CENTER,
            )
        )
    }


def status_of(conn: Connection, version_id: int, cc_id: int) -> Status:
    found = conn.execute(
        select(workflow_status.c.status).where(
            workflow_status.c.version_id == version_id,
            workflow_status.c.cost_center_id == cc_id,
        )
    ).scalar()
    return cast(Status, found or "not_started")


def _refusal(
    action: Action, user: User, can_write: bool, approver_ids: set[int], plans_it: bool
) -> str | None:
    return refusal(
        action,
        can_write=can_write,
        approves=user.user_id in approver_ids,
        controller=user.is_controller,
        plans_it=plans_it,
    )


def overview(engine: Engine, user: User, code: str, today: date) -> Overview:
    with unit_of_work(engine, user) as conn:
        r = get_round(conn, code)
        state = enabled_members(conn, r.version_id)
        rights = cost_center_rights(conn, user)
        enabled = {m for m, e in state.items() if e and m in rights}
        planned_by_me = plans(conn, user, sorted(enabled))
        info = {
            row.member_id: row
            for row in conn.execute(
                select(member.c.member_id, member.c.code, member.c.name, member.c.owner).where(
                    member.c.dim_id == COST_CENTER
                )
            )
        }
        statuses = {
            row.cost_center_id: row
            for row in conn.execute(
                select(workflow_status).where(workflow_status.c.version_id == r.version_id)
            )
        }
        children: dict[int | None, list[int]] = defaultdict(list)
        for m, parent in conn.execute(
            select(hier_edge.c.member_id, hier_edge.c.parent_id)
            .join(member, member.c.member_id == hier_edge.c.member_id)
            .where(hier_edge.c.hier_id == H_COST_CENTER)
            .order_by(hier_edge.c.sort_order, member.c.code)
        ):
            children[parent].append(m)
        assigned = approvers(conn, sorted(enabled))
        valid = valid_cost_centers(conn, r.fiscal_year) if user.is_controller else []

    rows: list[StatusRow] = []

    def visit(node: int, depth: int) -> dict[Status, int]:
        """Append the node's rows, depth first; returns its counts (empty: nothing enabled)."""
        m = info[node]
        if node not in children:
            if node not in enabled:
                return {}
            s = statuses.get(node)
            status = cast(Status, s.status if s else "not_started")
            ids = {u for u, _ in assigned.get(node, [])}
            actions = [
                a
                for a in ACTIONS
                if r.status == "open"
                and allows(a, status)
                and _refusal(a, user, rights[node], ids, node in planned_by_me) is None
            ]
            rows.append(
                StatusRow(
                    "cost_center",
                    m.code,
                    m.name,
                    depth,
                    status=status,
                    owner=m.owner,
                    approvers=[login for _, login in assigned.get(node, [])],
                    changed_at=s.changed_at if s else None,
                    changed_by=s.changed_by if s else None,
                    overdue=is_overdue(status, r.deadline, today),
                    actions=actions,
                )
            )
            return {status: 1}
        at = len(rows)
        rows.append(StatusRow("node", m.code, m.name, depth))
        counts: dict[Status, int] = {}
        for child in children[node]:
            for status, n in visit(child, depth + 1).items():
                counts[status] = counts.get(status, 0) + n
        if not counts:
            del rows[at:]
            return {}
        rows[at].counts = {s: counts.get(s, 0) for s in STATUSES}
        return counts

    for root in children[None]:
        visit(root, 0)
    not_planned = [
        NotPlanned(info[m].code, info[m].name, "new" if m not in state else "disabled")
        for m in valid
        if m not in enabled
    ]
    not_planned.sort(key=lambda n: n.code)
    return Overview(r, rows, not_planned)


def act(
    engine: Engine,
    actor: User,
    code: str,
    action: Action,
    cost_centers: list[str] | None = None,
    node: str | None = None,
    comment: str | None = None,
) -> list[str]:
    """Apply ``action`` to the named cost centers, or to every enabled cost center below
    ``node`` that the action applies to and the actor may act on (bulk approve). All or
    nothing; returns the codes."""
    if (cost_centers is None) == (node is None):
        raise WorkflowError("name either cost centers or a node")
    with unit_of_work(engine, actor) as conn:
        r = get_round(conn, code, lock="share")
        if r.status != "open":
            raise WorkflowError(f"the planning round is {r.status}")
        state = enabled_members(conn, r.version_id)
        if node is not None:
            node_id = conn.execute(
                select(member.c.member_id).where(
                    member.c.dim_id == COST_CENTER, member.c.code == node
                )
            ).scalar()
            if node_id is None:
                raise NotFoundError(f"node {node} not found")
            below = conn.execute(
                select(closure.c.descendant_id).where(
                    closure.c.hier_id == H_COST_CENTER, closure.c.ancestor_id == node_id
                )
            ).scalars()
            ids = [m for m in below if state.get(m)]
        else:
            assert cost_centers is not None
            found = dict(
                conn.execute(
                    select(member.c.code, member.c.member_id).where(
                        member.c.dim_id == COST_CENTER,
                        member.c.is_group.is_(False),
                        member.c.code.in_(cost_centers),
                    )
                ).all()
            )
            missing = sorted(set(cost_centers) - set(found))
            if missing:
                raise NotFoundError(f"cost center(s) {', '.join(missing)} not found")
            not_enabled = sorted(c for c, m in found.items() if not state.get(m))
            if not_enabled:
                raise WorkflowError(f"not enabled in the round: {', '.join(not_enabled)}")
            ids = list(found.values())
        if not ids:
            raise WorkflowError("no enabled cost centers")
        rights = cost_center_rights(conn, actor)
        mine = plans(conn, actor, ids)
        assigned = approvers(conn, ids)

        def why_not(cc: int) -> str | None:
            if cc not in rights:
                return "you can't see this cost center"
            approver_ids = {u for u, _ in assigned.get(cc, [])}
            return _refusal(action, actor, rights[cc], approver_ids, cc in mine)

        if node is not None:
            ids = [
                m
                for m in ids
                if allows(action, status_of(conn, r.version_id, m)) and why_not(m) is None
            ]
            if not ids:
                raise WorkflowError(f"nothing below {node} you may {action}")
        conn.execute(
            pg_insert(workflow_status)
            .values(
                [
                    {"version_id": r.version_id, "cost_center_id": m, "status": "not_started"}
                    for m in ids
                ]
            )
            .on_conflict_do_nothing()
        )
        current = conn.execute(
            select(workflow_status.c.cost_center_id, workflow_status.c.status, member.c.code)
            .join(member, member.c.member_id == workflow_status.c.cost_center_id)
            .where(
                workflow_status.c.version_id == r.version_id,
                workflow_status.c.cost_center_id.in_(ids),
            )
            .order_by(member.c.code)
            .with_for_update(of=workflow_status)
        ).all()
        changes: list[tuple[int, Status, Status]] = []
        for cc_id, status, cc_code in current:
            reason = why_not(cc_id)
            if reason is not None:
                raise AccessError(f"{cc_code}: {reason}")
            try:
                changes.append((cc_id, status, transition(status, action, comment)))
            except WorkflowError as e:
                raise WorkflowError(f"{cc_code}: {e}") from None
        for target in {t for _, _, t in changes}:
            conn.execute(
                update(workflow_status)
                .where(
                    workflow_status.c.version_id == r.version_id,
                    workflow_status.c.cost_center_id.in_([m for m, _, t in changes if t == target]),
                )
                .values(status=target, changed_at=func.now(), changed_by=actor.login)
            )
        conn.execute(
            insert(workflow_event),
            [
                {
                    "version_id": r.version_id,
                    "cost_center_id": m,
                    "actor": actor.login,
                    "from_status": s,
                    "to_status": t,
                    "comment": comment,
                }
                for m, s, t in changes
            ],
        )
        return [c.code for c in current]
