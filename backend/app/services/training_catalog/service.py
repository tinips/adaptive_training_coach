"""Validate and atomically publish reusable catalog knowledge."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Capability,
    ContextExecutionOption,
    ExecutionOptionCapability,
    GoalTemplate,
    GoalTemplateContext,
    TrainingContext,
)
from app.domain.enums import (
    CapabilityImportance,
    CatalogItemSource,
    CatalogItemStatus,
    Discipline,
    ExecutionOptionRole,
    GoalContextRole,
    GoalTemplateKind,
)
from app.schemas.catalog_expansion import (
    CapabilityRequirementProposal,
    ContextCapabilityOutput,
    GoalContextMappingOutput,
    GoalContextProposal,
    GoalTemplateDraft,
)

_CATALOG_ADVISORY_LOCK = 8_517_322_611_904_271_311
_FORBIDDEN_TEXT = re.compile(
    r"https?://|www\.|\b(?:buy|purchase|order)\b|"
    r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}(?::\d{2})?\b|"
    r"\b(?:my|mine|i am|i'm|athlete's)\b",
    flags=re.IGNORECASE,
)


class CatalogExpansionError(RuntimeError):
    """Safe validation or collision error for one expansion attempt."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CatalogPublicationResult:
    template_ids: dict[str, uuid.UUID]
    created_template_codes: tuple[str, ...]


class TrainingCatalogPublicationService:
    """Own semantic validation and one all-or-nothing catalog write."""

    async def publish(
        self,
        *,
        session: AsyncSession,
        templates: tuple[GoalTemplateDraft, ...],
        context_mapping: GoalContextMappingOutput | None,
        capability_definition: ContextCapabilityOutput | None,
    ) -> CatalogPublicationResult:
        self.validate(
            templates=templates,
            context_mapping=context_mapping,
            capability_definition=capability_definition,
        )
        await self._lock_catalog(session)

        template_codes = {item.code for item in templates}
        existing_templates = {
            item.code: item
            for item in await session.scalars(
                select(GoalTemplate).where(GoalTemplate.code.in_(template_codes))
            )
        }
        for draft in templates:
            existing = existing_templates.get(draft.code)
            if existing is not None and existing.kind is not draft.kind:
                raise CatalogExpansionError("goal_template_code_collision")
            if existing is not None and existing.status is not CatalogItemStatus.ACTIVE:
                raise CatalogExpansionError("goal_template_disabled")

        mapping_by_template = (
            {item.template_code: item for item in context_mapping.templates}
            if context_mapping is not None
            else {}
        )
        created_template_codes: list[str] = []
        template_rows: dict[str, GoalTemplate] = dict(existing_templates)
        for draft in templates:
            if draft.code in template_rows:
                continue
            mapping = mapping_by_template.get(draft.code)
            if mapping is None:
                raise CatalogExpansionError("missing_goal_context_mapping")
            template_row = GoalTemplate(
                id=uuid.uuid4(),
                code=draft.code,
                kind=draft.kind,
                display_name=draft.display_name,
                description=draft.description,
                source=CatalogItemSource.LLM_GENERATED,
                status=CatalogItemStatus.ACTIVE,
                definition_version=1,
            )
            session.add(template_row)
            template_rows[draft.code] = template_row
            created_template_codes.append(draft.code)

        proposals = {
            context.code: context
            for mapping in mapping_by_template.values()
            for context in mapping.contexts
        }
        context_codes = set(proposals)
        if capability_definition is not None:
            context_codes.update(
                option.execution_context_code
                for definition in capability_definition.contexts
                for option in definition.options
            )
        existing_contexts = {
            item.code: item
            for item in await session.scalars(
                select(TrainingContext).where(TrainingContext.code.in_(context_codes))
            )
        }
        for code, existing_context_row in existing_contexts.items():
            if existing_context_row.status is not CatalogItemStatus.ACTIVE:
                raise CatalogExpansionError(
                    "training_context_code_collision"
                    if code in proposals
                    else "unknown_execution_context"
                )
        context_rows: dict[str, TrainingContext] = dict(existing_contexts)
        created_context_codes: set[str] = set()
        for code, proposal in proposals.items():
            existing_context = existing_contexts.get(code)
            if existing_context is not None:
                if (
                    existing_context.discipline is not proposal.discipline
                    or existing_context.status is not CatalogItemStatus.ACTIVE
                ):
                    raise CatalogExpansionError("training_context_code_collision")
                continue
            if proposal.decision != "CREATE":
                raise CatalogExpansionError("unknown_training_context")
            context_row = TrainingContext(
                id=uuid.uuid4(),
                code=proposal.code,
                display_name=proposal.display_name,
                description=proposal.description,
                discipline=proposal.discipline,
                source=CatalogItemSource.LLM_GENERATED,
                status=CatalogItemStatus.ACTIVE,
                definition_version=1,
            )
            session.add(context_row)
            context_rows[code] = context_row
            created_context_codes.add(code)

        for code in context_codes:
            if code not in context_rows:
                raise CatalogExpansionError("unknown_execution_context")

        # Persist catalog parents before inserting rows that reference their UUIDs.
        # The execution option table has two FKs to training_contexts, so relying on
        # SQLAlchemy's mapper ordering alone is not sufficient on PostgreSQL when
        # the IDs are assigned directly instead of through ORM relationships.
        if created_template_codes or created_context_codes:
            await session.flush()

        for template_code in created_template_codes:
            template = template_rows[template_code]
            for proposal in mapping_by_template[template_code].contexts:
                session.add(
                    GoalTemplateContext(
                        goal_template_id=template.id,
                        training_context_id=context_rows[proposal.code].id,
                        role=proposal.role,
                        priority=proposal.priority,
                    )
                )

        if created_context_codes:
            if capability_definition is None:
                raise CatalogExpansionError("missing_context_capabilities")
            await self._publish_context_definitions(
                session=session,
                context_rows=context_rows,
                created_context_codes=created_context_codes,
                output=capability_definition,
            )

        await session.flush()
        return CatalogPublicationResult(
            template_ids={
                code: catalog_template.id
                for code, catalog_template in template_rows.items()
            },
            created_template_codes=tuple(sorted(created_template_codes)),
        )

    @classmethod
    def validate_context_mapping(
        cls,
        *,
        templates: tuple[GoalTemplateDraft, ...],
        context_mapping: GoalContextMappingOutput,
        active_contexts: dict[str, Discipline],
    ) -> tuple[GoalContextProposal, ...]:
        template_by_code = {item.code: item for item in templates}
        if not templates or {
            item.template_code for item in context_mapping.templates
        } != set(template_by_code):
            raise CatalogExpansionError("invalid_goal_context_mapping")
        proposals: dict[str, GoalContextProposal] = {}
        for mapping in context_mapping.templates:
            template = template_by_code[mapping.template_code]
            if len({item.code for item in mapping.contexts}) != len(mapping.contexts):
                raise CatalogExpansionError("duplicate_goal_context")
            if template.kind is GoalTemplateKind.PRIMARY and not any(
                item.role is GoalContextRole.TARGET for item in mapping.contexts
            ):
                raise CatalogExpansionError("primary_target_context_required")
            for item in mapping.contexts:
                if (
                    template.kind is GoalTemplateKind.SUPPORTING
                    and item.role is not GoalContextRole.SUPPORTING
                ):
                    raise CatalogExpansionError("invalid_supporting_context_role")
                existing_discipline = active_contexts.get(item.code)
                if item.decision == "USE_EXISTING":
                    if (
                        existing_discipline is None
                        or existing_discipline is not item.discipline
                    ):
                        raise CatalogExpansionError("unknown_training_context")
                elif existing_discipline is not None:
                    raise CatalogExpansionError("training_context_code_collision")
                else:
                    if item.display_name is None or item.description is None:
                        raise CatalogExpansionError("invalid_training_context")
                    cls._validate_general_text(item.display_name, item.description)
                previous = proposals.get(item.code)
                if previous is not None and (
                    previous.decision != item.decision
                    or previous.discipline is not item.discipline
                ):
                    raise CatalogExpansionError("inconsistent_training_context")
                proposals[item.code] = item
        return tuple(proposals.values())

    @classmethod
    def validate(
        cls,
        *,
        templates: tuple[GoalTemplateDraft, ...],
        context_mapping: GoalContextMappingOutput | None,
        capability_definition: ContextCapabilityOutput | None,
    ) -> None:
        if not templates or len({item.code for item in templates}) != len(templates):
            raise CatalogExpansionError("invalid_goal_templates")
        for template_draft in templates:
            cls._validate_general_text(
                template_draft.display_name,
                template_draft.description,
            )

        if context_mapping is None:
            if capability_definition is not None:
                raise CatalogExpansionError("unexpected_context_capabilities")
            return
        template_by_code = {item.code: item for item in templates}
        if {item.template_code for item in context_mapping.templates} != set(
            template_by_code
        ):
            raise CatalogExpansionError("invalid_goal_context_mapping")

        proposals: dict[str, GoalContextProposal] = {}
        new_context_codes: set[str] = set()
        for context_set in context_mapping.templates:
            template = template_by_code[context_set.template_code]
            if len({context.code for context in context_set.contexts}) != len(
                context_set.contexts
            ):
                raise CatalogExpansionError("duplicate_goal_context")
            if template.kind is GoalTemplateKind.PRIMARY and not any(
                context.role is GoalContextRole.TARGET
                for context in context_set.contexts
            ):
                raise CatalogExpansionError("primary_target_context_required")
            for context_proposal in context_set.contexts:
                if (
                    template.kind is GoalTemplateKind.SUPPORTING
                    and context_proposal.role is not GoalContextRole.SUPPORTING
                ):
                    raise CatalogExpansionError("invalid_supporting_context_role")
                if context_proposal.decision == "CREATE":
                    if (
                        context_proposal.display_name is None
                        or context_proposal.description is None
                    ):
                        raise CatalogExpansionError("invalid_training_context")
                    cls._validate_general_text(
                        context_proposal.display_name,
                        context_proposal.description,
                    )
                    new_context_codes.add(context_proposal.code)
                previous = proposals.get(context_proposal.code)
                if previous is not None and (
                    previous.decision != context_proposal.decision
                    or previous.discipline is not context_proposal.discipline
                ):
                    raise CatalogExpansionError("inconsistent_training_context")
                proposals[context_proposal.code] = context_proposal

        if not new_context_codes:
            if capability_definition is not None:
                raise CatalogExpansionError("unexpected_context_capabilities")
            return
        if capability_definition is None:
            raise CatalogExpansionError("missing_context_capabilities")
        definitions = {
            item.target_context_code: item for item in capability_definition.contexts
        }
        if len(definitions) != len(capability_definition.contexts):
            raise CatalogExpansionError("duplicate_context_definition")
        if set(definitions) != new_context_codes:
            raise CatalogExpansionError("invalid_context_definition_scope")

        capability_by_code = {
            item.code: item for item in capability_definition.capabilities
        }
        if len(capability_by_code) != len(capability_definition.capabilities):
            raise CatalogExpansionError("duplicate_capability")
        referenced_capabilities: set[str] = set()
        for context_code, definition in definitions.items():
            del context_code
            if len({item.code for item in definition.options}) != len(
                definition.options
            ):
                raise CatalogExpansionError("duplicate_execution_option")
            if not any(
                item.role is ExecutionOptionRole.PREFERRED
                for item in definition.options
            ):
                raise CatalogExpansionError("preferred_execution_required")
            for option in definition.options:
                cls._validate_general_text(option.display_name)
                if len(set(option.limitations)) != len(option.limitations):
                    raise CatalogExpansionError("duplicate_limitation")
                for limitation in option.limitations:
                    if len(limitation) > 160:
                        raise CatalogExpansionError("limitation_too_long")
                    cls._validate_general_text(limitation)
                if not any(
                    requirement.importance is CapabilityImportance.REQUIRED
                    for requirement in option.requirements
                ):
                    raise CatalogExpansionError("required_capability_missing")
                requirement_codes = {
                    requirement.capability_code for requirement in option.requirements
                }
                if len(requirement_codes) != len(option.requirements):
                    raise CatalogExpansionError("duplicate_capability_requirement")
                referenced_capabilities.update(requirement_codes)
        if not referenced_capabilities.issubset(capability_by_code):
            raise CatalogExpansionError("unknown_capability_reference")
        if referenced_capabilities != set(capability_by_code):
            raise CatalogExpansionError("unused_capability_proposal")
        for capability_proposal in capability_definition.capabilities:
            if capability_proposal.decision == "CREATE":
                if (
                    capability_proposal.display_name is None
                    or capability_proposal.description is None
                ):
                    raise CatalogExpansionError("invalid_capability")
                cls._validate_general_text(
                    capability_proposal.display_name,
                    capability_proposal.description,
                )

    @staticmethod
    async def _publish_context_definitions(
        *,
        session: AsyncSession,
        context_rows: dict[str, TrainingContext],
        created_context_codes: set[str],
        output: ContextCapabilityOutput,
    ) -> None:
        capability_proposals = {item.code: item for item in output.capabilities}
        existing_capabilities = {
            item.code: item
            for item in await session.scalars(
                select(Capability).where(Capability.code.in_(set(capability_proposals)))
            )
        }
        capability_rows: dict[str, Capability] = dict(existing_capabilities)
        for code, capability_proposal in capability_proposals.items():
            existing = existing_capabilities.get(code)
            if existing is not None:
                if (
                    existing.kind is not capability_proposal.kind
                    or existing.status is not CatalogItemStatus.ACTIVE
                ):
                    raise CatalogExpansionError("capability_code_collision")
                continue
            if capability_proposal.decision != "CREATE":
                raise CatalogExpansionError("unknown_capability_reference")
            row = Capability(
                id=uuid.uuid4(),
                code=code,
                display_name=capability_proposal.display_name,
                description=capability_proposal.description,
                kind=capability_proposal.kind,
                source=CatalogItemSource.LLM_GENERATED,
                status=CatalogItemStatus.ACTIVE,
                definition_version=1,
            )
            session.add(row)
            capability_rows[code] = row

        if capability_rows:
            await session.flush()

        definitions = {item.target_context_code: item for item in output.contexts}
        option_requirements: list[
            tuple[ContextExecutionOption, tuple[CapabilityRequirementProposal, ...]]
        ] = []
        for context_code in created_context_codes:
            definition = definitions[context_code]
            target = context_rows[context_code]
            for option_proposal in definition.options:
                option = ContextExecutionOption(
                    id=uuid.uuid4(),
                    target_context_id=target.id,
                    execution_context_id=context_rows[
                        option_proposal.execution_context_code
                    ].id,
                    code=option_proposal.code,
                    display_name=option_proposal.display_name,
                    role=option_proposal.role,
                    priority=option_proposal.priority,
                    limitations=option_proposal.limitations,
                )
                session.add(option)
                option_requirements.append(
                    (option, tuple(option_proposal.requirements))
                )

        if option_requirements:
            await session.flush()
        for option, requirements in option_requirements:
            for requirement in requirements:
                session.add(
                    ExecutionOptionCapability(
                        execution_option_id=option.id,
                        capability_id=capability_rows[requirement.capability_code].id,
                        importance=requirement.importance,
                    )
                )

    @staticmethod
    async def _lock_catalog(session: AsyncSession) -> None:
        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": _CATALOG_ADVISORY_LOCK},
            )

    @staticmethod
    def _validate_general_text(*values: str) -> None:
        if any(_FORBIDDEN_TEXT.search(value) for value in values):
            raise CatalogExpansionError("non_general_catalog_text")
