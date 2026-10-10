from tests.api.conftest import Api


async def test_health_and_lifespan(api: Api) -> None:
    async with api.client() as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "town_square" in api.app.state.content.rooms
