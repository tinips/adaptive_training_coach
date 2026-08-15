"""Resolve goal contexts against an athlete's current capabilities."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass

from app.db.models import (
    Capability,
    ContextExecutionOption,
    ExecutionOptionCapability,
    GoalTemplateContext,
    TrainingContext,
)
from app.domain.enums import (
    AthleteCapabilityStatus,
    CapabilityImportance,
    ContextAssessmentStatus,
    ExecutionOptionRole,
    GoalContextRole,
)
from app.repositories.athlete_capabilities import AthleteCapabilityRepository
from app.repositories.training_catalog import TrainingCatalogRepository
from app.schemas.capabilities import (
    AvailableExecution,
    CapabilityOption,
    CapabilityReview,
    CapabilityReviewContext,
    ContextExecutionAssessment,
    GoalExecutionAssessment,
)

_IMPORTANCE_ORDER = {
    CapabilityImportance.REQUIRED: 0,
    CapabilityImportance.RECOMMENDED: 1,
    CapabilityImportance.OPTIONAL: 2,
}
_ROLE_ORDER = {
    ExecutionOptionRole.PREFERRED: 0,
    ExecutionOptionRole.SUBSTITUTE: 1,
}


@dataclass(frozen=True, slots=True)
class _CatalogBundle:
    contexts: tuple[tuple[GoalTemplateContext, TrainingContext], ...]
    options: tuple[tuple[ContextExecutionOption, TrainingContext, TrainingContext], ...]
    requirements: tuple[tuple[ExecutionOptionCapability, Capability], ...]


class CapabilityAssessmentService:
    async def review(
        self,
        *,
        catalog: TrainingCatalogRepository,
        athlete_capabilities: AthleteCapabilityRepository,
        athlete_id: uuid.UUID,
        goal_template_id: uuid.UUID | None,
        supporting_goal_template_id: uuid.UUID | None,
    ) -> CapabilityReview | None:
        bundle = await self._load(
            catalog=catalog,
            goal_template_id=goal_template_id,
            supporting_goal_template_id=supporting_goal_template_id,
        )
        if not bundle.contexts or not bundle.options:
            return None
        states = await athlete_capabilities.states(athlete_id=athlete_id)
        return self._build_review(bundle=bundle, states=states)

    async def save_and_assess(
        self,
        *,
        catalog: TrainingCatalogRepository,
        athlete_capabilities: AthleteCapabilityRepository,
        athlete_id: uuid.UUID,
        goal_template_id: uuid.UUID | None,
        supporting_goal_template_id: uuid.UUID | None,
        review: CapabilityReview,
        selected_ids: set[uuid.UUID],
    ) -> GoalExecutionAssessment:
        reviewed_ids = {option.id for option in review.options}
        if not selected_ids.issubset(reviewed_ids):
            raise ValueError("capability selection is outside the current review")
        await athlete_capabilities.replace_reviewed(
            athlete_id=athlete_id,
            reviewed_ids=reviewed_ids,
            available_ids=selected_ids,
        )
        return await self.assess(
            catalog=catalog,
            athlete_capabilities=athlete_capabilities,
            athlete_id=athlete_id,
            goal_template_id=goal_template_id,
            supporting_goal_template_id=supporting_goal_template_id,
        )

    async def assess(
        self,
        *,
        catalog: TrainingCatalogRepository,
        athlete_capabilities: AthleteCapabilityRepository,
        athlete_id: uuid.UUID,
        goal_template_id: uuid.UUID | None,
        supporting_goal_template_id: uuid.UUID | None,
    ) -> GoalExecutionAssessment:
        bundle = await self._load(
            catalog=catalog,
            goal_template_id=goal_template_id,
            supporting_goal_template_id=supporting_goal_template_id,
        )
        states = await athlete_capabilities.states(athlete_id=athlete_id)
        return self._build_assessment(bundle=bundle, states=states)

    @staticmethod
    async def _load(
        *,
        catalog: TrainingCatalogRepository,
        goal_template_id: uuid.UUID | None,
        supporting_goal_template_id: uuid.UUID | None,
    ) -> _CatalogBundle:
        goal_ids = {
            value
            for value in (goal_template_id, supporting_goal_template_id)
            if value is not None
        }
        contexts = await catalog.contexts_for_goals(goal_template_ids=goal_ids)
        context_ids = {context.id for _, context in contexts}
        options = await catalog.execution_options(context_ids=context_ids)
        option_ids = {option.id for option, _, _ in options}
        requirements = await catalog.option_requirements(option_ids=option_ids)
        return _CatalogBundle(
            contexts=contexts,
            options=options,
            requirements=requirements,
        )

    @staticmethod
    def _unique_contexts(
        rows: tuple[tuple[GoalTemplateContext, TrainingContext], ...],
    ) -> tuple[tuple[GoalTemplateContext, TrainingContext], ...]:
        chosen: dict[uuid.UUID, tuple[GoalTemplateContext, TrainingContext]] = {}
        for relation, context in rows:
            current = chosen.get(context.id)
            if current is None or (
                current[0].role is GoalContextRole.SUPPORTING
                and relation.role is GoalContextRole.TARGET
            ):
                chosen[context.id] = (relation, context)
        return tuple(
            sorted(chosen.values(), key=lambda item: (item[0].priority, item[1].code))
        )

    @classmethod
    def _build_review(
        cls,
        *,
        bundle: _CatalogBundle,
        states: dict[uuid.UUID, AthleteCapabilityStatus],
    ) -> CapabilityReview:
        contexts = cls._unique_contexts(bundle.contexts)
        context_by_id = {context.id: context for _, context in contexts}
        option_meta = {
            option.id: (option, target)
            for option, target, _ in bundle.options
            if target.id in context_by_id
        }
        capability_rows: dict[uuid.UUID, Capability] = {}
        importance: dict[uuid.UUID, CapabilityImportance] = {}
        roles: dict[uuid.UUID, set[ExecutionOptionRole]] = defaultdict(set)
        target_codes: dict[uuid.UUID, set[str]] = defaultdict(set)
        for requirement, capability in bundle.requirements:
            meta = option_meta.get(requirement.execution_option_id)
            if meta is None:
                continue
            option, target = meta
            capability_rows[capability.id] = capability
            previous = importance.get(capability.id)
            if (
                previous is None
                or _IMPORTANCE_ORDER[requirement.importance]
                < _IMPORTANCE_ORDER[previous]
            ):
                importance[capability.id] = requirement.importance
            roles[capability.id].add(option.role)
            target_codes[capability.id].add(target.code)
        options = tuple(
            CapabilityOption(
                id=capability.id,
                code=capability.code,
                display_name=capability.display_name,
                kind=capability.kind,
                importance=importance[capability.id],
                execution_roles=tuple(
                    sorted(
                        roles[capability.id],
                        key=lambda role: _ROLE_ORDER[role],
                    )
                ),
                target_context_codes=tuple(sorted(target_codes[capability.id])),
                selected=(
                    states.get(capability.id) is AthleteCapabilityStatus.AVAILABLE
                ),
            )
            for capability in sorted(
                capability_rows.values(),
                key=lambda item: (item.kind, item.display_name),
            )
        )
        return CapabilityReview(
            contexts=tuple(
                CapabilityReviewContext(
                    code=context.code,
                    display_name=context.display_name,
                    role=relation.role,
                )
                for relation, context in contexts
            ),
            options=options,
        )

    @classmethod
    def _build_assessment(
        cls,
        *,
        bundle: _CatalogBundle,
        states: dict[uuid.UUID, AthleteCapabilityStatus],
    ) -> GoalExecutionAssessment:
        requirements_by_option: dict[
            uuid.UUID, list[tuple[ExecutionOptionCapability, Capability]]
        ] = defaultdict(list)
        for requirement, capability in bundle.requirements:
            requirements_by_option[requirement.execution_option_id].append(
                (requirement, capability)
            )
        options_by_context: dict[
            uuid.UUID,
            list[tuple[ContextExecutionOption, TrainingContext]],
        ] = defaultdict(list)
        for option, target, execution in bundle.options:
            options_by_context[target.id].append((option, execution))

        assessments: list[ContextExecutionAssessment] = []
        for _, target in cls._unique_contexts(bundle.contexts):
            candidates = sorted(
                options_by_context[target.id],
                key=lambda item: (_ROLE_ORDER[item[0].role], item[0].priority),
            )
            available: list[tuple[ContextExecutionOption, TrainingContext]] = []
            potentially_available = False
            for option, execution in candidates:
                required = [
                    capability
                    for requirement, capability in requirements_by_option[option.id]
                    if requirement.importance is CapabilityImportance.REQUIRED
                ]
                statuses = [states.get(capability.id) for capability in required]
                if required and all(
                    status is AthleteCapabilityStatus.AVAILABLE for status in statuses
                ):
                    available.append((option, execution))
                elif required and all(
                    status is not AthleteCapabilityStatus.UNAVAILABLE
                    for status in statuses
                ):
                    potentially_available = True

            default = available[0] if available else None
            if default is not None and default[0].role is ExecutionOptionRole.PREFERRED:
                status = ContextAssessmentStatus.FEASIBLE
            elif default is not None:
                status = ContextAssessmentStatus.FEASIBLE_WITH_SUBSTITUTION
            elif potentially_available:
                status = ContextAssessmentStatus.UNKNOWN
            else:
                status = ContextAssessmentStatus.LIMITED

            reference = (
                default[0]
                if default is not None
                else (candidates[0][0] if candidates else None)
            )
            reference_requirements = requirements_by_option.get(
                reference.id if reference is not None else uuid.UUID(int=0), []
            )
            missing_required = tuple(
                capability.display_name
                for requirement, capability in reference_requirements
                if requirement.importance is CapabilityImportance.REQUIRED
                and states.get(capability.id) is not AthleteCapabilityStatus.AVAILABLE
            )
            missing_recommended = tuple(
                capability.display_name
                for requirement, capability in reference_requirements
                if requirement.importance is CapabilityImportance.RECOMMENDED
                and states.get(capability.id) is not AthleteCapabilityStatus.AVAILABLE
            )
            assessments.append(
                ContextExecutionAssessment(
                    target_context=target.code,
                    target_display_name=target.display_name,
                    status=status,
                    default_execution=(
                        default[1].code if default is not None else None
                    ),
                    available_executions=tuple(
                        AvailableExecution(
                            code=execution.code,
                            display_name=option.display_name,
                            role=option.role,
                            limitations=tuple(option.limitations),
                        )
                        for option, execution in available
                    ),
                    missing_required=missing_required,
                    missing_recommended=missing_recommended,
                    limitations=(
                        tuple(default[0].limitations) if default is not None else ()
                    ),
                )
            )
        return GoalExecutionAssessment(contexts=tuple(assessments))
