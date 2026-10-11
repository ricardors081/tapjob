"""Unit and isolated tests for Inbound Envelope Claims and Lease Fencing (Findings 1, 2).

Proves:
1. ACCEPTED attempts=0 -> claim -> PROCESSING attempts=1
2. Hard crash (worker dies without persisting FAILED) -> lease expires -> reclaim -> attempts=2
3. Repeated crashes until MAX_INBOUND_RETRIES -> terminal FAILED, no 4th claim
4. Normal transient failure does not double-increment attempt count
5. Active owner (lease unexpired) cannot be reclaimed
6. Expired owner is reclaimable
7. Worker A (stale token A) vs Worker B (fresh token B) fencing:
   - Worker A cannot mark PROCESSED after being reclaimed
   - Worker A cannot mark FAILED after being reclaimed
   - Worker B succeeds in completing processing
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy import Table, create_engine
from sqlalchemy.orm import Session, sessionmaker

from tapjob_api.messaging.inbound import (
    InboundEffect,
    InboundEffectKind,
    InboundEffectState,
    ProviderEventEnvelope,
    ProviderEventProcessingStatus,
    ProviderEventType,
)
from tapjob_api.messaging.inbound_service import (
    MAX_INBOUND_RETRIES,
    InboundClaimResult,
    claim_inbound_envelope,
    complete_inbound_effect,
    get_inbound_effect,
    mark_event_failed,
    mark_event_processed,
    mark_expired_envelope_dead_letter,
    record_inbound_effect,
    reserve_inbound_effect,
)


@pytest.fixture
def sqlite_session() -> Iterator[Session]:
    """Provides an in-memory SQLite session with all tables created."""
    engine = create_engine("sqlite:///:memory:")
    cast(Table, ProviderEventEnvelope.__table__).create(engine)
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()
    yield session
    session.close()
    engine.dispose()


def test_inbound_first_claim_increments_attempt(sqlite_session: Session) -> None:
    """1. ACCEPTED attempts=0 -> claim -> PROCESSING attempts=1 (Finding 1)."""
    envelope = ProviderEventEnvelope(
        id=101,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_claim_1",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.ACCEPTED,
        normalized_event={"sender_external_id": "5511999999999", "text": "oi"},
        processing_attempts=0,
    )
    sqlite_session.add(envelope)
    sqlite_session.commit()

    claimed_env, token, result = claim_inbound_envelope(
        sqlite_session, envelope_id=envelope.id, lease_seconds=60.0
    )
    sqlite_session.commit()

    assert result == InboundClaimResult.CLAIMED
    assert claimed_env is not None
    assert token is not None
    assert claimed_env.status == ProviderEventProcessingStatus.PROCESSING
    assert claimed_env.processing_attempts == 1
    assert claimed_env.processing_lease_token == token
    assert claimed_env.processing_lease_expires_at is not None


def test_inbound_hard_crash_consumes_attempt_and_reclaims_until_max(
    sqlite_session: Session,
) -> None:
    """2 & 3. Hard crashes consume attempts; at max retries row is terminal (Finding 1)."""
    envelope = ProviderEventEnvelope(
        id=102,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_crash_loop",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.ACCEPTED,
        normalized_event={"sender_external_id": "5511999999999", "text": "oi"},
        processing_attempts=0,
    )
    sqlite_session.add(envelope)
    sqlite_session.commit()

    # Attempt 1: Worker 1 claims and abruptly dies (no mark_event_failed called!)
    env1, token1, res1 = claim_inbound_envelope(
        sqlite_session, envelope_id=envelope.id, lease_seconds=10.0
    )
    sqlite_session.commit()
    assert res1 == InboundClaimResult.CLAIMED
    assert env1 is not None and env1.processing_attempts == 1

    # Simulate lease expiry without worker cleanup
    env1.processing_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    sqlite_session.commit()

    # Attempt 2: Worker 2 reclaims and also abruptly dies
    env2, token2, res2 = claim_inbound_envelope(
        sqlite_session, envelope_id=envelope.id, lease_seconds=10.0
    )
    sqlite_session.commit()
    assert res2 == InboundClaimResult.CLAIMED
    assert env2 is not None and env2.processing_attempts == 2
    assert token2 != token1

    # Simulate lease expiry again
    env2.processing_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    sqlite_session.commit()

    # Attempt 3: Worker 3 reclaims (reaches MAX_INBOUND_RETRIES = 3)
    env3, _token3, res3 = claim_inbound_envelope(
        sqlite_session, envelope_id=envelope.id, lease_seconds=10.0
    )
    sqlite_session.commit()
    assert res3 == InboundClaimResult.CLAIMED
    assert env3 is not None and env3.processing_attempts == 3

    # Simulate lease expiry after 3rd crash
    env3.processing_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    sqlite_session.commit()

    # Attempt 4: Should NOT be granted! Must transition to terminal FAILED
    env4, token4, res4 = claim_inbound_envelope(
        sqlite_session, envelope_id=envelope.id, lease_seconds=10.0
    )
    sqlite_session.commit()
    assert res4 == InboundClaimResult.MAX_ATTEMPTS_EXHAUSTED
    assert token4 is None
    assert env4 is not None
    assert env4.status == ProviderEventProcessingStatus.FAILED
    assert env4.error_details is not None
    assert env4.error_details.get("retryable") is False
    assert env4.processing_lease_token is None


def test_inbound_transient_failure_does_not_duplicate_attempt_increment(
    sqlite_session: Session,
) -> None:
    """4. Normal exception handling marks FAILED without double attempts (Finding 1)."""
    envelope = ProviderEventEnvelope(
        id=103,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_transient_no_double",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.ACCEPTED,
        normalized_event={"sender_external_id": "5511999999999", "text": "oi"},
        processing_attempts=0,
    )
    sqlite_session.add(envelope)
    sqlite_session.commit()

    # Claim increments attempts to 1
    claimed_env, token, res = claim_inbound_envelope(sqlite_session, envelope_id=envelope.id)
    sqlite_session.commit()
    assert res == InboundClaimResult.CLAIMED
    assert claimed_env is not None
    assert claimed_env.processing_attempts == 1

    # Transient error occurs: mark_event_failed records failure
    err_details = {
        "error": "Storage connection reset",
        "retryable": True,
        "attempts": claimed_env.processing_attempts,  # 1
        "max_attempts": MAX_INBOUND_RETRIES,
    }
    assert token is not None
    fenced = mark_event_failed(
        sqlite_session,
        envelope_id=envelope.id,
        fencing_token=token,
        error_details=err_details,
        envelope=claimed_env,
    )
    sqlite_session.commit()

    assert fenced is True
    assert claimed_env.status == ProviderEventProcessingStatus.FAILED
    # Attempts count remains strictly 1 (no double increment!)
    assert claimed_env.processing_attempts == 1
    assert claimed_env.error_details is not None
    assert claimed_env.error_details.get("attempts") == 1


def test_inbound_active_owner_cannot_be_reclaimed(sqlite_session: Session) -> None:
    """5. While lease is still unexpired, another claim returns ACTIVE_LEASE (Finding 2)."""
    envelope = ProviderEventEnvelope(
        id=104,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_active_lease",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.ACCEPTED,
        normalized_event={"sender_external_id": "5511999999999", "text": "oi"},
        processing_attempts=0,
    )
    sqlite_session.add(envelope)
    sqlite_session.commit()

    # Worker A claims with 60s lease
    _env_a, _token_a, res_a = claim_inbound_envelope(
        sqlite_session, envelope_id=envelope.id, lease_seconds=60.0
    )
    sqlite_session.commit()
    assert res_a == InboundClaimResult.CLAIMED

    # Worker B attempts to claim immediately -> ACTIVE_LEASE
    _env_b, token_b, res_b = claim_inbound_envelope(
        sqlite_session, envelope_id=envelope.id, lease_seconds=60.0
    )
    assert res_b == InboundClaimResult.ACTIVE_LEASE
    assert token_b is None


def test_inbound_stale_owner_fencing_blocks_stale_processed_and_failed(
    sqlite_session: Session,
) -> None:
    """7-10. Worker A (stale) cannot mark PROCESSED or FAILED after Worker B claims (Finding 2)."""
    envelope = ProviderEventEnvelope(
        id=105,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_fencing_race",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.ACCEPTED,
        normalized_event={"sender_external_id": "5511999999999", "text": "oi"},
        processing_attempts=0,
    )
    sqlite_session.add(envelope)
    sqlite_session.commit()

    # Worker A claims with token A
    env_a, token_a, res_a = claim_inbound_envelope(
        sqlite_session, envelope_id=envelope.id, lease_seconds=10.0
    )
    sqlite_session.commit()
    assert res_a == InboundClaimResult.CLAIMED
    assert token_a is not None
    assert env_a is not None

    # Worker A experiences long GC pause / slow network; lease expires
    env_a.processing_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    sqlite_session.commit()

    # Worker B reclaims with token B
    env_b, token_b, res_b = claim_inbound_envelope(
        sqlite_session, envelope_id=envelope.id, lease_seconds=60.0
    )
    sqlite_session.commit()
    assert res_b == InboundClaimResult.CLAIMED
    assert token_b is not None
    assert token_b != token_a

    # Now Worker A wakes up and attempts to mark PROCESSED using stale token_a
    success_a = mark_event_processed(
        sqlite_session,
        envelope_id=envelope.id,
        fencing_token=token_a,
    )
    assert success_a is False  # FENCED OUT!

    # Worker A attempts to mark FAILED using stale token_a
    fail_a = mark_event_failed(
        sqlite_session,
        envelope_id=envelope.id,
        fencing_token=token_a,
        error_details={"error": "Worker A timed out"},
    )
    assert fail_a is False  # FENCED OUT!

    # Worker B completes successfully using valid token_b
    success_b = mark_event_processed(
        sqlite_session,
        envelope_id=envelope.id,
        fencing_token=token_b,
        envelope=env_b,
    )
    sqlite_session.commit()
    assert success_b is True
    assert env_b is not None
    assert env_b.status == ProviderEventProcessingStatus.PROCESSED


def test_owner_transition_without_token_raises_error(sqlite_session: Session) -> None:
    """Transitioning out of PROCESSING without a fencing token raises ValueError (Finding 3)."""
    envelope = ProviderEventEnvelope(
        id=201,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_missing_token",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.ACCEPTED,
        normalized_event={"sender_external_id": "5511999999999", "text": "oi"},
        processing_attempts=0,
    )
    sqlite_session.add(envelope)
    sqlite_session.commit()

    _claimed_env, _token, res = claim_inbound_envelope(
        sqlite_session, envelope_id=envelope.id, lease_seconds=60.0
    )
    assert res == InboundClaimResult.CLAIMED

    with pytest.raises(ValueError, match="fencing_token is required"):
        mark_event_processed(sqlite_session, envelope_id=envelope.id, fencing_token="")

    with pytest.raises(ValueError, match="fencing_token is required"):
        mark_event_failed(
            sqlite_session,
            envelope_id=envelope.id,
            fencing_token="",
            error_details={"error": "none"},
        )


def test_owner_transition_incorrect_token_returns_false(sqlite_session: Session) -> None:
    """Transitioning out of PROCESSING with incorrect token returns False (Finding 3)."""
    envelope = ProviderEventEnvelope(
        id=202,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_incorrect_token",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.ACCEPTED,
        normalized_event={"sender_external_id": "5511999999999", "text": "oi"},
        processing_attempts=0,
    )
    sqlite_session.add(envelope)
    sqlite_session.commit()

    _claimed_env, _token, res = claim_inbound_envelope(
        sqlite_session, envelope_id=envelope.id, lease_seconds=60.0
    )
    assert res == InboundClaimResult.CLAIMED

    # Attempt to transition with wrong token
    processed = mark_event_processed(
        sqlite_session, envelope_id=envelope.id, fencing_token="wrong_token_123"
    )
    assert processed is False

    failed = mark_event_failed(
        sqlite_session,
        envelope_id=envelope.id,
        fencing_token="wrong_token_123",
        error_details={"error": "wrong"},
    )
    assert failed is False


def test_expired_envelope_dead_letter_guarantees(sqlite_session: Session) -> None:
    """Sweeper dead letter transition mutates expired lease, never active owner (Finding 3)."""
    envelope = ProviderEventEnvelope(
        id=203,
        provider="evolution",
        provider_instance="default",
        external_event_id="evt_dead_letter_test",
        event_type=ProviderEventType.MESSAGE_RECEIVED,
        status=ProviderEventProcessingStatus.ACCEPTED,
        normalized_event={"sender_external_id": "5511999999999", "text": "oi"},
        processing_attempts=0,
    )
    sqlite_session.add(envelope)
    sqlite_session.commit()

    # Active claim (60s lease)
    _claimed_env, _token, res = claim_inbound_envelope(
        sqlite_session, envelope_id=envelope.id, lease_seconds=60.0
    )
    assert res == InboundClaimResult.CLAIMED

    # Sweeper cannot dead-letter while lease is active
    active_swept = mark_expired_envelope_dead_letter(
        sqlite_session,
        envelope_id=envelope.id,
        error_details={"error": "swept"},
    )
    assert active_swept is False

    # Simulate lease expiry
    envelope.processing_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    sqlite_session.commit()

    # Now sweeper succeeds
    expired_swept = mark_expired_envelope_dead_letter(
        sqlite_session,
        envelope_id=envelope.id,
        error_details={"error": "swept"},
    )
    assert expired_swept is True
    sqlite_session.commit()

    sqlite_session.refresh(envelope)
    assert envelope.status == ProviderEventProcessingStatus.FAILED
    assert envelope.processing_lease_token is None


def test_inbound_effect_does_not_cross_envelopes_with_same_message_id() -> None:
    """Verifies InboundEffect lookup is strictly scoped to envelope_id (Finding 3).

    Creates:
    - Envelope A + message_id SAME + User A -> has RESUME_IMPORT effect
    - Envelope B + message_id SAME + User B -> lookup must return None
    - B records its own distinct effect
    - Both effects exist independently, zero cross-user/cross-envelope reuse.
    """
    engine = create_engine("sqlite:///:memory:")
    cast(Table, ProviderEventEnvelope.__table__).create(engine)
    cast(Table, InboundEffect.__table__).create(engine)
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()

    try:
        same_msg_id = "wamid_shared_msg_123"

        env_a = ProviderEventEnvelope(
            id=1001,
            provider="evolution",
            provider_instance="inst_a",
            external_event_id="evt_a_123",
            event_type=ProviderEventType.MESSAGE_RECEIVED,
            status=ProviderEventProcessingStatus.ACCEPTED,
            normalized_event={"provider_message_id": same_msg_id, "text": "curriculo.pdf"},
        )
        env_b = ProviderEventEnvelope(
            id=1002,
            provider="evolution",
            provider_instance="inst_b",
            external_event_id="evt_b_123",
            event_type=ProviderEventType.MESSAGE_RECEIVED,
            status=ProviderEventProcessingStatus.ACCEPTED,
            normalized_event={"provider_message_id": same_msg_id, "text": "curriculo.pdf"},
        )
        session.add_all([env_a, env_b])
        session.commit()

        # A has RESUME_IMPORT effect
        import_id_a = str(uuid.uuid4())
        record_inbound_effect(
            session,
            envelope_id=env_a.id,
            inbound_message_id=same_msg_id,
            effect_kind=InboundEffectKind.RESUME_IMPORT.value,
            resource_id=import_id_a,
            effect_payload={"facts_count": 5},
        )
        session.commit()

        # Lookup for B MUST return None
        effect_for_b = get_inbound_effect(
            session,
            envelope_id=env_b.id,
            effect_kind=InboundEffectKind.RESUME_IMPORT.value,
        )
        assert effect_for_b is None

        # B creates its own distinct effect
        import_id_b = str(uuid.uuid4())
        record_inbound_effect(
            session,
            envelope_id=env_b.id,
            inbound_message_id=same_msg_id,
            effect_kind=InboundEffectKind.RESUME_IMPORT.value,
            resource_id=import_id_b,
            effect_payload={"facts_count": 3},
        )
        session.commit()

        # Both effects exist independently
        effect_a = get_inbound_effect(
            session,
            envelope_id=env_a.id,
            effect_kind=InboundEffectKind.RESUME_IMPORT.value,
        )
        effect_b = get_inbound_effect(
            session,
            envelope_id=env_b.id,
            effect_kind=InboundEffectKind.RESUME_IMPORT.value,
        )
        assert effect_a is not None and effect_b is not None
        assert effect_a.envelope_id == env_a.id
        assert effect_b.envelope_id == env_b.id
        assert effect_a.resource_id == import_id_a
        assert effect_b.resource_id == import_id_b
        assert effect_a.id != effect_b.id
    finally:
        session.close()
        engine.dispose()


def test_complete_inbound_effect_cannot_change_reserved_resource() -> None:
    """Reserve A -> Tentar complete com B -> Rejeitado;
    resource permanece A; state não é corrompido.
    """
    engine = create_engine("sqlite:///:memory:")
    cast(Table, ProviderEventEnvelope.__table__).create(engine)
    cast(Table, InboundEffect.__table__).create(engine)
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()

    try:
        env = ProviderEventEnvelope(
            id=2001,
            provider="evolution",
            provider_instance="default",
            external_event_id="evt_res_immut",
            event_type=ProviderEventType.MESSAGE_RECEIVED,
            status=ProviderEventProcessingStatus.ACCEPTED,
            normalized_event={"text": "confirmar"},
        )
        session.add(env)
        session.commit()

        res_a = str(uuid.uuid4())
        res_b = str(uuid.uuid4())

        # 1. Reserve A
        effect = reserve_inbound_effect(
            session,
            envelope_id=env.id,
            inbound_message_id="msg_res_1",
            effect_kind=InboundEffectKind.FINALIZE_REVIEW.value,
            resource_id=res_a,
        )
        session.commit()
        assert effect.resource_id == res_a
        assert effect.operation_state == InboundEffectState.PENDING.value

        # 2. Tentar complete com B -> raises ValueError
        with pytest.raises(ValueError, match="Inbound effect resource_id mismatch"):
            complete_inbound_effect(
                session,
                envelope_id=env.id,
                effect_kind=InboundEffectKind.FINALIZE_REVIEW.value,
                expected_resource_id=res_b,
                effect_payload={"success": True},
            )
        session.rollback()

        # 3. Resource permanece A, state continua PENDING
        reloaded = get_inbound_effect(
            session,
            envelope_id=env.id,
            effect_kind=InboundEffectKind.FINALIZE_REVIEW.value,
        )
        assert reloaded is not None
        assert reloaded.resource_id == res_a
        assert reloaded.operation_state == InboundEffectState.PENDING.value

        # 4. Complete com A -> Sucesso
        completed = complete_inbound_effect(
            session,
            envelope_id=env.id,
            effect_kind=InboundEffectKind.FINALIZE_REVIEW.value,
            expected_resource_id=res_a,
            effect_payload={"profile_version": 1},
        )
        session.commit()
        assert completed.resource_id == res_a
        assert completed.operation_state == InboundEffectState.COMPLETED.value
    finally:
        session.close()
        engine.dispose()
