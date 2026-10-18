"""TapJob Agent Factory — Canonical deterministic parallel execution planner."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from tools.agent_factory.mission_contract import MissionContract
from tools.agent_factory.task_contract import TaskContract


class PlannerError(Exception):
    """Base error for parallel planning failures."""


class DependencyCycleError(PlannerError):
    """Raised when task dependencies form a cycle."""


class MissingDependencyError(PlannerError):
    """Raised when a task depends on a task_id that is not in the mission."""


@dataclass
class ExecutionGroup:
    """A deterministic execution group of tasks to be executed sequentially or concurrently."""

    group_index: int
    task_ids: list[str]
    parallel: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutionPlan:
    """Deterministic, reproducible execution plan for a MissionContract."""

    mission_id: str
    groups: list[ExecutionGroup]
    task_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "groups": [g.to_dict() for g in self.groups],
            "task_count": self.task_count,
        }


class MissionPlanner:
    """Generates a deterministic parallel execution plan enforcing all AF invariants."""

    @classmethod
    def plan_mission(cls, mission: MissionContract) -> ExecutionPlan:
        """Create a deterministic execution plan for the given MissionContract.

        Fails closed on missing dependencies, dependency cycles, or invalid contracts.
        """
        mission.validate()

        tasks_by_id: dict[str, TaskContract] = {t.task_id: t for t in mission.tasks}
        all_task_ids = set(tasks_by_id.keys())

        # Validate dependency references
        for tid, task in tasks_by_id.items():
            for dep in task.dependencies:
                if dep not in all_task_ids:
                    err_msg = (
                        f"Task '{tid}' depends on '{dep}', which is not part of mission "
                        f"'{mission.mission_id}'."
                    )
                    raise MissingDependencyError(err_msg)
                if dep == tid:
                    raise DependencyCycleError(f"Task '{tid}' cannot depend on itself.")

        # Detect dependency cycles using depth-first search
        visited: dict[str, int] = {}  # 0: visiting, 1: visited

        def check_cycle(curr: str, path: list[str]) -> None:
            visited[curr] = 0
            for dep in tasks_by_id[curr].dependencies:
                if visited.get(dep) == 0:
                    cycle = " -> ".join([*path, dep])
                    raise DependencyCycleError(f"Dependency cycle detected in mission: {cycle}")
                if dep not in visited:
                    check_cycle(dep, [*path, dep])
            visited[curr] = 1

        for tid in sorted(all_task_ids):
            if tid not in visited:
                check_cycle(tid, [tid])

        # Compute dependency layers (topological levels)
        # depth(t) = 0 if no dependencies, else 1 + max(depth(dep) for dep in dependencies)
        task_depths: dict[str, int] = {}

        def get_depth(curr: str) -> int:
            if curr in task_depths:
                return task_depths[curr]
            deps = tasks_by_id[curr].dependencies
            if not deps:
                task_depths[curr] = 0
                return 0
            d = 1 + max(get_depth(dep) for dep in deps)
            task_depths[curr] = d
            return d

        for tid in sorted(all_task_ids):
            get_depth(tid)

        # Group by topological depth
        max_depth = max(task_depths.values()) if task_depths else 0
        depth_buckets: list[list[str]] = [[] for _ in range(max_depth + 1)]
        for tid, d in task_depths.items():
            depth_buckets[d].append(tid)

        groups: list[ExecutionGroup] = []
        group_idx = 0

        # For each depth layer, group tasks into parallel or serial execution groups
        for layer in depth_buckets:
            # Deterministic sorting within layer
            sorted_candidates = sorted(layer)
            remaining_in_layer = list(sorted_candidates)

            while remaining_in_layer:
                # Select next task
                candidate_id = remaining_in_layer.pop(0)
                candidate_task = tasks_by_id[candidate_id]

                # Check if candidate is safe to run in parallel
                is_parallel_safe = (
                    candidate_task.parallel_safe
                    and candidate_task.contracts_frozen
                    and len(candidate_task.touches_critical_serial_paths()) == 0
                )

                if not is_parallel_safe:
                    # Serialized execution for safety
                    groups.append(
                        ExecutionGroup(
                            group_index=group_idx,
                            task_ids=[candidate_id],
                            parallel=False,
                        )
                    )
                    group_idx += 1
                    continue

                # Parallel-safe candidate: try to build a concurrent group
                current_group_ids = [candidate_id]
                still_unassigned: list[str] = []

                for other_id in remaining_in_layer:
                    other_task = tasks_by_id[other_id]
                    other_parallel_safe = (
                        other_task.parallel_safe
                        and other_task.contracts_frozen
                        and len(other_task.touches_critical_serial_paths()) == 0
                    )

                    if not other_parallel_safe:
                        # Cannot join a parallel group
                        still_unassigned.append(other_id)
                        continue

                    # Check collision with all existing tasks in current_group_ids
                    has_collision = False
                    for existing_id in current_group_ids:
                        existing_task = tasks_by_id[existing_id]
                        if candidate_task.check_ownership_conflict(
                            other_task
                        ) or existing_task.check_ownership_conflict(other_task):
                            has_collision = True
                            break

                    if not has_collision:
                        current_group_ids.append(other_id)
                    else:
                        still_unassigned.append(other_id)

                remaining_in_layer = still_unassigned
                groups.append(
                    ExecutionGroup(
                        group_index=group_idx,
                        task_ids=current_group_ids,
                        parallel=len(current_group_ids) > 1,
                    )
                )
                group_idx += 1

        return ExecutionPlan(
            mission_id=mission.mission_id,
            groups=groups,
            task_count=len(mission.tasks),
        )
