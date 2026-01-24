import requests
import sys

BACKEND_URL = "https://truedy.closi.tech/api/v1"
TEST_ORIGIN = "https://truedy.sendora.ai"

def test_cors():
    print(f"Testing CORS against {BACKEND_URL}")
    print(f"Origin: {TEST_ORIGIN}")
    print("-" * 50)
    
    # Test 1: Health Endpoint (GET)
    try:
        response = requests.get(
            f"{BACKEND_URL}/health",
            headers={"Origin": TEST_ORIGIN}
        )
        print(f"GET /health Status: {response.status_code}")
        cors_header = response.headers.get("Access-Control-Allow-Origin")
        print(f"Access-Control-Allow-Origin: {cors_header}")
        
        if cors_header == TEST_ORIGIN or cors_header == "*":
            print("✅ GET CORS Passed")
        else:
            print("❌ GET CORS Failed")
    except Exception as e:
        print(f"GET Request Failed: {e}")
        
    print("-" * 50)

    # Test 2: Preflight (OPTIONS)
    try:
        response = requests.options(
            f"{BACKEND_URL}/health",
            headers={
                "Origin": TEST_ORIGIN,
                "Access-Control-Request-Method": "GET"
            }
        )
        print(f"OPTIONS /health Status: {response.status_code}")
        cors_header = response.headers.get("Access-Control-Allow-Origin")
        print(f"Access-Control-Allow-Origin: {cors_header}")
        
        if cors_header == TEST_ORIGIN or cors_header == "*":
            print("✅ OPTIONS CORS Passed")
        else:
            print("❌ OPTIONS CORS Failed")

    except Exception as e:
        print(f"OPTIONS Request Failed: {e}")

if __name__ == "__main__":
    test_cors()
