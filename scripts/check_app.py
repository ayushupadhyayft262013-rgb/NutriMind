import sys
import os
sys.path.append(os.getcwd())

try:
    from app.main import app
    print("Successfully imported app.main")
    
    # List routes to verify
    print("Routes:")
    for route in app.routes:
        print(f"- {route.path} ({route.name})")

except Exception as e:
    print(f"Error importing app: {e}")
    sys.exit(1)
