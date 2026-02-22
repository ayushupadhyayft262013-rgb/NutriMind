# NutriMind Comprehensive Testing Strategy

To ensure high reliability and to catch subtle bugs (like broken frontend links or authentication mismatches) before they reach production, we need a robust, automated testing strategy.

The strategy is broken down into four distinct layers:

---

## 1. Unit Testing (The Foundation)
**Goal:** Test individual functions, database models, and helper utilities in isolation.
**Tools:** `pytest`, `pytest-asyncio`

*   **Database Models (`test_models.py`):** Verify `SQLModel` schemas constraints, defaults, and relationships without spinning up the app server.
*   **Authentication Logic (`test_auth.py`):** Ensure JWT encoding/decoding, token expiration warnings, and password hashing work correctly.
*   **Business Logic (`test_utils.py`):** Test specialized utilities (e.g., macro distribution calculators, date formatting functions) providing edge-case inputs.

## 2. Integration Testing (The Glue)
**Goal:** Verify that the FastAPI endpoints interact properly with the SQLite database, external services (Gemini), and authentication middleware.
**Tools:** `pytest`, `httpx` (Async Client), `pytest-env`

*   **API Routes (`test_api.py`):**
    *   Test adding, editing, and deleting meals (`POST`, `PUT`, `DELETE /api/meals`).
    *   Verify the return codes (200 OK vs 401 Unauthorized vs 404 Not Found).
    *   *Bug Prevented:* The issue where the backend silently didn't commit edits would be caught here because the test fetches the DB immediately after the `PUT` request.
*   **Authentication Flow (`test_login.py`):**
    *   Test successful and failed mock logins.
    *   Verify the secure `session_token` cookie is set correctly and persists across requests.

## 3. End-to-End (E2E) UI Testing (The User Experience)
**Goal:** Simulate actual browser interactions (clicking buttons, filling forms) to ensure the frontend templates and JavaScript logic are wired correctly to the backend.
**Tools:** `Playwright` (for Python)

*   **User Journeys (`test_ui_flows.py`):**
    *   **Login to Dashboard:** Script a Chromium hidden browser to load `/login`, enter a mock Telegram ID, and verify the `/dashboard` HTML fully renders.
    *   **User Switching Flow:** Script a click on the User Switcher dropdown, select a different user, and assert the URL changes correctly and the new stats load.
    *   *Bug Prevented:* The previous User Swither bug (using old query params instead of the API endpoint) would immediately crash a Playwright test because the requested page wouldn't load or wouldn't authorize properly.
*   **Form Submissions:** Fill out the "Profile Settings" UI form, click Save, and check if the database updated.

## 4. Continuous Integration (CI) Automation
**Goal:** Never allow broken code into the main branch.
**Tools:** `GitHub Actions`

*   **Automated Verification (`.github/workflows/test.yml`):**
    *   Every time you `git push`, GitHub spins up a fresh environment.
    *   It installs dependencies, runs the `pytest` Unit/Integration tests, and executes the Playwright E2E browser tests.
    *   If any test fails, the deployment is **blocked** and you get an alert.

---

### Implementation Steps (Next Actions)

If you'd like to implement this, we can take the following steps:
1.  **Install Testing Tools:** `pip install pytest pytest-asyncio httpx playwright`
2.  **Create `tests/` Folder:** Refactor our current `scripts/test_uat.py` into formal `pytest` files.
3.  **Write the Playwright Script:** Create an automated script that clicks through the User Switcher and tests the Meal Editor UI.
4.  **Add GitHub Action:** Write a YAML file so tests run automatically when you push code to GitHub.

Let me know if you want to set this up now!
