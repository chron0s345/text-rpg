import dataclasses

from httpx import ASGITransport, AsyncClient

from app.main import create_app
from tests.api.conftest import Api, settings_for


async def test_health_and_lifespan(api: Api) -> None:
    async with api.client() as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "town_square" in api.app.state.content.rooms


async def test_lifespan_with_world_ticker(database_url: str) -> None:
    settings = dataclasses.replace(settings_for(database_url), world_ticker=True)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            assert (await client.get("/health")).status_code == 200
    # shutdown stopped the ticker task and released its lock (otherwise this would hang/fail)
