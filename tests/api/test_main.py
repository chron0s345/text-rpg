from httpx import AsyncClient

from tests.api.conftest import Api


async def test_health_and_lifespan(api: Api, client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "town_square" in api.app.state.content.rooms
    assert api.services.presence is not None
