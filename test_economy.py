import pytest
from gullycric.src.services.economy import economy_service

@pytest.mark.asyncio
async def test_economy_rewards_idempotency():
    user = await economy_service.get_or_create_user(555, "GullyHero", "Hero")
    start_diamonds = user.diamonds

    # First credit with idempotency key
    key = "match_win_555_123"
    res1 = await economy_service.add_rewards(555, diamonds=50, idempotency_key=key)
    assert res1 is True

    # Duplicate credit must be rejected
    res2 = await economy_service.add_rewards(555, diamonds=50, idempotency_key=key)
    assert res2 is False

    u_after = await economy_service.get_or_create_user(555)
    assert u_after.diamonds == start_diamonds + 50
