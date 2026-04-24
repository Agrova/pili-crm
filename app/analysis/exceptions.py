"""Domain exceptions for the analysis module (ADR-011 Task 2)."""

from __future__ import annotations


class AnalysisError(Exception):
    """Base for analysis-module errors (distinct from ValueError and friends)."""


class MultipleCustomersForChatError(AnalysisError):
    """Raised by ``get_customer_for_chat`` when the same chat links to ≥2
    customers via ``communications_link`` rows with
    ``target_module='orders'`` / ``target_entity='orders_customer'``.

    The analyzer cannot pick a customer deterministically in this case —
    ``apply_analysis_to_customer`` catches this and returns an
    ``AnalysisApplicationResult`` with ``ambiguous_customer_ids`` populated
    so operators can resolve the ambiguity manually.
    """

    def __init__(self, *, chat_id: int, customer_ids: list[int]) -> None:
        self.chat_id = chat_id
        self.customer_ids = list(customer_ids)
        super().__init__(
            f"chat_id={chat_id} is linked to {len(customer_ids)} customers "
            f"({customer_ids!r}); analyzer cannot disambiguate"
        )


class AnalysisAlreadyAppliedError(AnalysisError):
    """Raised by ``apply_analysis_to_customer`` when the same
    ``(analyzer_version, chat_id)`` already has entries in
    ``analysis_created_entities`` and the caller did not pass ``force=True``.

    Re-application is blocked by default because profile updates are
    idempotent (dedup over ``source_message_ids`` + normalized fallback) but
    draft-order creation is not — repeating it would spawn duplicate
    ``orders_order`` rows. Callers must opt into re-application explicitly
    with ``force=True``, which triggers rollback of the prior analyzer-created
    drafts before re-creation.
    """

    def __init__(
        self,
        *,
        analysis_id: int,
        analyzer_version: str,
        chat_id: int,
        existing_entity_count: int,
    ) -> None:
        self.analysis_id = analysis_id
        self.analyzer_version = analyzer_version
        self.chat_id = chat_id
        self.existing_entity_count = existing_entity_count
        super().__init__(
            f"analysis already applied for analyzer_version="
            f"{analyzer_version!r}, chat_id={chat_id} "
            f"({existing_entity_count} created_entities); "
            f"use force=True to re-apply"
        )
