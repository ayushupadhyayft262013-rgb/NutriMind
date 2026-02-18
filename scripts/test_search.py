import sys
import os
import asyncio
from httpx import AsyncClient, ASGITransport

sys.path.append(os.getcwd())

async def test_search():
    from app.main import app
    # Use ASGITransport for direct app testing
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        print("Testing /api/search_food...")
        response = await client.get("/api/search_food?q=apple")
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")

if __name__ == "__main__":
    asyncio.run(test_search())
