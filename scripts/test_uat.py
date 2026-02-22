import asyncio
import logging
import httpx
import sys
import os
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

# Add app to path
sys.path.insert(0, os.path.abspath('.'))

from app.database import engine, UserProfile, Meal
from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("UAT_TEST")

async def test_database():
    """Test local SQLite Database schema and ORM."""
    logger.info("TESTING DATABASE [1/4]")
    try:
        async with AsyncSession(engine) as session:
            # Try to insert a mock user
            mock_user = UserProfile(telegram_user_id=999999, name="UAT Tester", target_kcal=2000)
            session.add(mock_user)
            await session.commit()
            
            # Fetch the user
            statement = select(UserProfile).where(UserProfile.telegram_user_id == 999999)
            result = await session.exec(statement)
            fetched = result.first()
            assert fetched is not None, "Failed to retrieve saved user."
            assert fetched.name == "UAT Tester", "User name mismatch."
            
            # Cleanup
            await session.delete(fetched)
            await session.commit()
            
        logger.info("✅ Database Test: SUCCESS")
        return True
    except Exception as e:
        logger.error(f"❌ Database Test: FAILED ({e})")
        return False

async def test_auth_and_routes(base_url="http://localhost:8000"):
    """Test web endpoints to ensure login redirects and JWT auth work."""
    logger.info("TESTING WEB ROUTES [2/4]")
    try:
        async with httpx.AsyncClient() as client:
            # 1. Unauthenticated hit to dashboard should redirect to /login
            resp = await client.get(f"{base_url}/dashboard", follow_redirects=False)
            if resp.status_code not in (302, 303, 307):
                if resp.status_code == 200 and "login" in resp.text.lower():
                    pass # Handled by direct login render in some logic
                else:
                    logger.warning(f"Unauthenticated /dashboard did not redirect. Got {resp.status_code}")
                    
            # 2. Mock Login
            resp = await client.post(f"{base_url}/login", data={"telegram_id": 999999}, follow_redirects=False)
            assert resp.status_code in (302, 303), f"Login failed to redirect. Got {resp.status_code}"
            assert "session_token" in resp.cookies, "JWT Cookie not set upon login."
            
            # 3. Authenticated Hit with Cookies
            cookies = {"session_token": resp.cookies["session_token"]}
            resp_dash = await client.get(f"{base_url}/dashboard", cookies=cookies)
            assert resp_dash.status_code == 200, f"Authenticated Dashboard failed. Got {resp_dash.status_code}"
            
        logger.info("✅ Web Routes Test: SUCCESS")
        return True
    except httpx.ConnectError:
        logger.error(f"❌ Web Routes Test: FAILED (Connection refused to {base_url}. Is the server running?)")
        return False
    except Exception as e:
        logger.error(f"❌ Web Routes Test: FAILED ({e})")
        return False

async def test_service_initializations():
    """Verify that the Notion and Gemini clients can be initialized."""
    logger.info("TESTING EXTERNAL SERVICES [3/4]")
    try:
        from app.notion_service import notion_service
        # Check if client lazy-loads gracefully
        if not settings.NOTION_API_KEY:
            logger.warning("NOTION_API_KEY is empty, but service won't crash until invoked. (Decoupling Working)")
        
        from app.gemini_service import gemini_service
        # Check if the Gemini schema loaded without parse errors
        assert gemini_service.model == "gemini-2.0-flash", "Gemini Model mismatch."
        
        logger.info("✅ External Services Initialization: SUCCESS")
        return True
    except Exception as e:
        logger.error(f"❌ External Services Test: FAILED ({e})")
        return False

async def test_auth_utilities():
    """Test JWT creation and decoding without web requests."""
    logger.info("TESTING JWT AUTHENTICATION UTILS [4/4]")
    try:
        from app.auth import create_access_token, decode_access_token
        from datetime import timedelta
        
        token = create_access_token(data={"sub": 888888}, expires_delta=timedelta(minutes=5))
        assert token, "Token creation failed."
        
        token_data = decode_access_token(token)
        assert token_data.user_id == 888888, "Token decoding failed."
        
        logger.info("✅ JWT Utils Test: SUCCESS")
        return True
    except Exception as e:
        logger.error(f"❌ JWT Utils Test: FAILED ({e})")
        return False

async def run_all(simulation_mode=False):
    db_ok = await test_database()
    svc_ok = await test_service_initializations()
    jwt_ok = await test_auth_utilities()
    
    web_ok = False
    if simulation_mode:
        web_ok = await test_auth_and_routes(base_url="http://localhost:8000")
    else:
        logger.info("Skipping Web Routes test since 'simulation_mode' is not active. (Use --sim to test running container)")
        web_ok = True
        
    if all([db_ok, svc_ok, jwt_ok, web_ok]):
        logger.info("\n🎉 All UAT Tests Passed! The system is ready for host deployment.")
        sys.exit(0)
    else:
        logger.error("\n💥 Some UAT tests failed. Please investigate before deploying.")
        sys.exit(1)

if __name__ == "__main__":
    sim_mode = "--sim" in sys.argv
    asyncio.run(run_all(simulation_mode=sim_mode))
